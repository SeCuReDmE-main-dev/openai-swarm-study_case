import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import sys

from openai.types.chat.chat_completion import ChatCompletion, Choice

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from swarm import Agent, Swarm  # noqa: E402
from swarm.types import (  # noqa: E402
    ChatCompletionMessage,
    ChatCompletionMessageToolCall,
    Function,
    Result,
)


CCP_CONTEXT_ID = "_ccp_context_id"
CCP_VERSION = "_ccp_version"
CCP_TIMESTAMP = "_ccp_timestamp"
CCP_PROVENANCE = "_ccp_provenance"

STORE_PATH = Path(__file__).with_name("ccp_store.json")
STALE_PATH = Path(__file__).with_name("ccp_store_stale.json")


def create_mock_response(message: Dict[str, Any], function_calls=None, model="gpt-4o"):
    calls = function_calls or []
    role = message.get("role", "assistant")
    content = message.get("content", "")
    tool_calls = (
        [
            ChatCompletionMessageToolCall(
                id=f"mock_tc_id_{idx}",
                type="function",
                function=Function(
                    name=call.get("name", ""),
                    arguments=json.dumps(call.get("args", {})),
                ),
            )
            for idx, call in enumerate(calls, start=1)
        ]
        if calls
        else None
    )
    return ChatCompletion(
        id="mock_cc_id",
        created=1234567890,
        model=model,
        object="chat.completion",
        choices=[
            Choice(
                message=ChatCompletionMessage(
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                ),
                finish_reason="stop",
                index=0,
            )
        ],
    )


class _MockCompletions:
    def __init__(self, responses):
        self._responses = responses
        self._index = 0

    def create(self, **kwargs):
        if self._index >= len(self._responses):
            raise RuntimeError("No more mock responses configured.")
        response = self._responses[self._index]
        self._index += 1
        return response


class MockOpenAIClient:
    def __init__(self, responses):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _MockCompletions(responses)


class LocalJsonContextStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Persisted context must be a JSON object.")
        return data

    def save(self, context: Dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(context, handle, indent=2, sort_keys=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_stale(timestamp: str, max_age_minutes: int) -> bool:
    parsed = datetime.fromisoformat(timestamp)
    age = datetime.now(timezone.utc) - parsed
    return age > timedelta(minutes=max_age_minutes)


def validate_context(context: Dict[str, Any], max_age_minutes: int = 60) -> Dict[str, Any]:
    if not context:
        return context
    ts = context.get(CCP_TIMESTAMP)
    if not isinstance(ts, str):
        raise ValueError("Persisted context missing a valid _ccp_timestamp.")
    if is_stale(ts, max_age_minutes):
        raise ValueError(
            f"Persisted context is stale (older than {max_age_minutes} minute(s))."
        )
    return context


def merge_context(persisted: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    return {**persisted, **incoming}


def run_with_ccp(
    client: Swarm,
    agent: Agent,
    messages,
    incoming_context: Dict[str, Any],
    store: LocalJsonContextStore,
    validator: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    persisted = store.load()
    if validator:
        persisted = validator(persisted)
    merged_context = merge_context(persisted, copy.deepcopy(incoming_context))
    response = client.run(agent=agent, messages=messages, context_variables=merged_context)
    store.save(response.context_variables)
    return {
        "response": response,
        "merged_context": merged_context,
        "persisted_after_run": response.context_variables,
    }


def update_ccp_metadata(context_variables: Dict[str, Any]) -> Dict[str, Any]:
    context = copy.deepcopy(context_variables)
    current_version = int(context.get(CCP_VERSION, 0))
    context[CCP_VERSION] = current_version + 1
    context[CCP_TIMESTAMP] = utc_now_iso()
    context.setdefault(CCP_CONTEXT_ID, "ctx_phase1_demo")
    context.setdefault(
        CCP_PROVENANCE,
        {"source": "context_continuity_packet_example", "updated_by": "profile_agent"},
    )
    return context


def build_agent() -> Agent:
    def instructions(context_variables):
        user_name = context_variables.get("user_name", "User")
        topic = context_variables.get("task_topic", "context continuity")
        return (
            "You are a concise assistant. "
            f"The user's name is {user_name}. "
            f"Current topic is {topic}. "
            "Answer in one short paragraph."
        )

    def record_checkpoint(context_variables):
        updated = update_ccp_metadata(context_variables)
        return Result(
            value="Context continuity metadata updated.",
            context_variables=updated,
        )

    return Agent(
        name="ProfileAgent",
        instructions=instructions,
        functions=[record_checkpoint],
    )


def write_stale_fixture() -> None:
    stale_payload = {
        "user_name": "Jean",
        "task_topic": "stale-check",
        CCP_CONTEXT_ID: "ctx_phase1_demo",
        CCP_VERSION: 3,
        CCP_TIMESTAMP: (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).replace(microsecond=0).isoformat(),
        CCP_PROVENANCE: {"source": "fixture", "updated_by": "stale_test"},
    }
    with STALE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(stale_payload, handle, indent=2, sort_keys=True)


def main() -> None:
    STORE_PATH.unlink(missing_ok=True)
    STALE_PATH.unlink(missing_ok=True)

    responses = [
        create_mock_response(
            {"role": "assistant", "content": ""},
            function_calls=[{"name": "record_checkpoint", "args": {}}],
        ),
        create_mock_response(
            {"role": "assistant", "content": "Run A complete. Context continuity metadata recorded."}
        ),
        create_mock_response(
            {"role": "assistant", "content": ""},
            function_calls=[{"name": "record_checkpoint", "args": {}}],
        ),
        create_mock_response(
            {"role": "assistant", "content": "Run B complete. Persisted context was resumed and updated."}
        ),
    ]
    client = Swarm(client=MockOpenAIClient(responses))
    agent = build_agent()

    store = LocalJsonContextStore(STORE_PATH)
    run_a = run_with_ccp(
        client=client,
        agent=agent,
        messages=[{"role": "user", "content": "Summarize what you know about me so far."}],
        incoming_context={"user_name": "Jean", "task_topic": "phase one ccp"},
        store=store,
        validator=lambda ctx: validate_context(ctx, max_age_minutes=60),
    )
    print("RUN A RESPONSE:")
    print(run_a["response"].messages[-1]["content"])
    print("RUN A PERSISTED CONTEXT:")
    print(json.dumps(run_a["persisted_after_run"], indent=2, sort_keys=True))

    run_b = run_with_ccp(
        client=client,
        agent=agent,
        messages=[{"role": "user", "content": "Continue and mention continuity in one sentence."}],
        incoming_context={"task_topic": "phase one ccp follow-up"},
        store=store,
        validator=lambda ctx: validate_context(ctx, max_age_minutes=60),
    )
    print("\nRUN B RESPONSE:")
    print(run_b["response"].messages[-1]["content"])
    print("RUN B MERGED CONTEXT:")
    print(json.dumps(run_b["merged_context"], indent=2, sort_keys=True))

    write_stale_fixture()
    stale_store = LocalJsonContextStore(STALE_PATH)
    print("\nSTALE CHECK:")
    try:
        run_with_ccp(
            client=client,
            agent=agent,
            messages=[{"role": "user", "content": "This should fail stale validation."}],
            incoming_context={"task_topic": "stale-reject"},
            store=stale_store,
            validator=lambda ctx: validate_context(ctx, max_age_minutes=60),
        )
    except ValueError as err:
        print(f"Validator rejected stale context as expected: {err}")


if __name__ == "__main__":
    main()
