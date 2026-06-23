#!/usr/bin/env python3
"""Generate the synthesized ``nested_agents`` test fixture for issue #213.

Mirrors the on-disk layout Claude Code 2.1.172+ leaves when sub-agents
spawn their own sub-agents, fully sanitized — no real paths, session ids,
or agent ids. The layout is FLAT at every depth:

    test/test_data/nested_agents/
      <trunk>.jsonl                       trunk transcript
      <trunk>/subagents/
        agent-<id>.jsonl       (×10)      one per agent, ANY nesting depth
        agent-<id>.meta.json   (×10)      {agentType, description, toolUseId}

Three sub-trees exercise the #213 mechanics:

- **2×2 fan-out**: trunk spawns mid1 + mid2 in parallel; each mid spawns
  two leaves. Leaf answers round-trip verbatim into the spawn tool_result
  (so the sidechain dedup collapses them) — EXCEPT leaf22, whose result is
  a truncated copy, so its transcript survives dedup and stays visible at
  depth 2 (the HTML assertions hang off it).
- **3-deep chain**: trunk → c1 → c2 → c3; each level reports its child's
  answer in a distinct wrapper, so every level survives dedup.
- **Interrupted spawn**: the trunk's last spawn was rejected — its
  tool_result is the generic is_error stub with NO ``toolUseResult`` and
  no ``agentId:`` tail. The transcript + sidecar exist on disk; only the
  sidecar's ``toolUseId`` links it (the meta-only path).

Nested spawn tool_results carry the in-band ``agentId: <id> (use
SendMessage …)`` tail but — faithfully to the real data — NO top-level
``toolUseResult`` (that enrichment is trunk-only).

Re-run to regenerate: ``python3 scripts/gen_nested_agents_fixture.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "test" / "test_data" / "nested_agents"

TRUNK_SID = "33330000-0000-4000-8000-000000000001"
TS = "2026-06-12T09:00:00.000Z"
VERSION = "2.1.173"

MID1, MID2 = "nsmid001", "nsmid002"
LEAF11, LEAF12, LEAF21, LEAF22 = "nsleaf11", "nsleaf12", "nsleaf21", "nsleaf22"
CHAIN1, CHAIN2, CHAIN3 = "nschain1", "nschain2", "nschain3"
INTR = "nsintr01"

LEAF_ANSWERS = {
    LEAF11: "Log files scroll past — each line a moment captured.",
    LEAF12: "6*7 = 42, six added together seven times.",
    LEAF21: "Cold caches wait in rows; one warm read and the index hums.",
    LEAF22: "9*8 = 72, computed as 9*(10-2) = 90 - 18.",
}
# leaf22's transcript survives the output dedup: the spawn tool_result
# carries a TRUNCATED copy of the answer, so the texts don't match.
LEAF_RESULTS = dict(LEAF_ANSWERS)
LEAF_RESULTS[LEAF22] = "9*8 = 72, computed as 9*(10-…"

CHAIN3_ANSWER = "depth 3: BOTTOM — stopping here, no further spawn."
CHAIN2_ANSWER = f"depth 2: child said: {CHAIN3_ANSWER}"
CHAIN1_ANSWER = f"depth 1: child said: {CHAIN2_ANSWER}"


def _base(uuid: str, parent: str | None, *, agent_id: str | None) -> dict:
    e: dict = {
        "type": "",  # set by caller
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": agent_id is not None,
        "userType": "external",
        "cwd": "/repo",
        "sessionId": TRUNK_SID,
        "version": VERSION,
        "timestamp": TS,
    }
    if agent_id is not None:
        e["agentId"] = agent_id
    return e


def _user(uuid, parent, content, *, agent_id=None, tool_use_result=None) -> dict:
    e = _base(uuid, parent, agent_id=agent_id)
    e["type"] = "user"
    e["message"] = {"role": "user", "content": content}
    if tool_use_result is not None:
        e["toolUseResult"] = tool_use_result
    return e


def _assistant(uuid, parent, content, *, agent_id=None) -> dict:
    e = _base(uuid, parent, agent_id=agent_id)
    e["type"] = "assistant"
    e["message"] = {
        "id": f"msg_{uuid}",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "end_turn",
        "content": content,
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    return e


def _spawn_use(tool_use_id: str, description: str, prompt: str) -> dict:
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": "Agent",
        "input": {
            "description": description,
            "subagent_type": "general-purpose",
            "prompt": prompt,
        },
    }


def _tail(agent_id: str) -> dict:
    return {
        "type": "text",
        "text": (
            f"agentId: {agent_id} (use SendMessage with to: '{agent_id}' "
            "to continue this agent)\n"
            "<usage>subagent_tokens: 999\ntool_uses: 0\nduration_ms: 1500</usage>"
        ),
    }


def _spawn_result(tool_use_id: str, agent_id: str, answer: str) -> list[dict]:
    return [
        {"type": "text", "text": answer},
        _tail(agent_id),
    ]


def _tu(agent_id: str) -> str:
    """The spawning tool_use id for an agent (one spawn per agent here)."""
    return f"toolu_ns_{agent_id}"


def _spawner_file(
    agent_id: str,
    prompt: str,
    spawns: list[tuple[str, str, str]],  # (child_id, description, child_prompt)
    answer: str,
) -> list[dict]:
    """An agent transcript that spawns children, then answers.

    Faithful nested shape: every entry carries the TRUNK sessionId,
    isSidechain=true and the agent's own id; spawn tool_results have the
    in-band tail but no toolUseResult.
    """
    p = f"{agent_id}-"
    rows = [_user(p + "u1", None, prompt, agent_id=agent_id)]
    uses = [_spawn_use(_tu(c), d, cp) for c, d, cp in spawns]
    rows.append(_assistant(p + "a1", p + "u1", uses, agent_id=agent_id))
    parent = p + "a1"
    for i, (child_id, _d, _p) in enumerate(spawns, 1):
        child_answer = (
            LEAF_RESULTS.get(child_id)
            or {CHAIN2: CHAIN2_ANSWER, CHAIN3: CHAIN3_ANSWER}[child_id]
        )
        rows.append(
            _user(
                f"{p}r{i}",
                parent,
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": _tu(child_id),
                        "content": _spawn_result(_tu(child_id), child_id, child_answer),
                    }
                ],
                agent_id=agent_id,
            )
        )
        parent = f"{p}r{i}"
    rows.append(
        _assistant(
            p + "a2", parent, [{"type": "text", "text": answer}], agent_id=agent_id
        )
    )
    return rows


def _leaf_file(agent_id: str, prompt: str) -> list[dict]:
    p = f"{agent_id}-"
    return [
        _user(p + "u1", None, prompt, agent_id=agent_id),
        _assistant(
            p + "a1",
            p + "u1",
            [{"type": "text", "text": LEAF_ANSWERS[agent_id]}],
            agent_id=agent_id,
        ),
    ]


def _trunk() -> list[dict]:
    mid_prompt = "Spawn two leaves and report both answers."
    return [
        _user(
            "ns-u1",
            None,
            [{"type": "text", "text": "Demonstrate nested agents (2x2 + chain)."}],
        ),
        _assistant(
            "ns-a1",
            "ns-u1",
            [
                {"type": "text", "text": "Spawning two mid-agents in parallel."},
                _spawn_use(_tu(MID1), "Mid-agent 1", mid_prompt),
                _spawn_use(_tu(MID2), "Mid-agent 2", mid_prompt),
            ],
        ),
        _user(
            "ns-r1",
            "ns-a1",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": _tu(MID1),
                    "content": _spawn_result(
                        _tu(MID1),
                        MID1,
                        f"L11 said: {LEAF_ANSWERS[LEAF11]}\nL12 said: {LEAF_ANSWERS[LEAF12]}",
                    ),
                }
            ],
            # Trunk-level spawns additionally get the structured enrichment.
            tool_use_result={"agentId": MID1, "status": "completed"},
        ),
        _user(
            "ns-r2",
            "ns-r1",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": _tu(MID2),
                    "content": _spawn_result(
                        _tu(MID2),
                        MID2,
                        f"L21 said: {LEAF_ANSWERS[LEAF21]}\nL22 said: {LEAF_ANSWERS[LEAF22]}",
                    ),
                }
            ],
            tool_use_result={"agentId": MID2, "status": "completed"},
        ),
        _assistant(
            "ns-a2",
            "ns-r2",
            [
                {"type": "text", "text": "Now the recursion chain."},
                _spawn_use(_tu(CHAIN1), "Chain depth 1", "Recurse to depth 3."),
            ],
        ),
        _user(
            "ns-r3",
            "ns-a2",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": _tu(CHAIN1),
                    "content": _spawn_result(_tu(CHAIN1), CHAIN1, CHAIN1_ANSWER),
                }
            ],
            tool_use_result={"agentId": CHAIN1, "status": "completed"},
        ),
        _assistant(
            "ns-a3",
            "ns-r3",
            [
                {"type": "text", "text": "One more spawn (will be interrupted)."},
                _spawn_use(_tu(INTR), "Doomed spawn", "Run forever."),
            ],
        ),
        # Interrupted spawn: generic rejection, is_error, NO toolUseResult,
        # no agentId tail — the sidecar's toolUseId is the only link.
        _user(
            "ns-r4",
            "ns-a3",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": _tu(INTR),
                    "is_error": True,
                    "content": (
                        "The user doesn't want to proceed with this tool use. "
                        "The tool use was rejected."
                    ),
                }
            ],
        ),
        _assistant(
            "ns-a4",
            "ns-r4",
            [{"type": "text", "text": "All scenarios done."}],
        ),
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    # Regenerate from scratch — stale files from a previous layout (renamed
    # agent ids, removed scenarios) must not survive in the fixture.
    shutil.rmtree(FIXTURE, ignore_errors=True)
    subagents = FIXTURE / TRUNK_SID / "subagents"

    _write_jsonl(FIXTURE / f"{TRUNK_SID}.jsonl", _trunk())

    leaf_prompt = "Answer your one-line task. Use no tools."
    files: dict[str, list[dict]] = {
        MID1: _spawner_file(
            MID1,
            "Spawn two leaves and report both answers.",
            [(LEAF11, "Leaf 1.1", leaf_prompt), (LEAF12, "Leaf 1.2", leaf_prompt)],
            f"L11 said: {LEAF_ANSWERS[LEAF11]}\nL12 said: {LEAF_ANSWERS[LEAF12]}",
        ),
        MID2: _spawner_file(
            MID2,
            "Spawn two leaves and report both answers.",
            [(LEAF21, "Leaf 2.1", leaf_prompt), (LEAF22, "Leaf 2.2", leaf_prompt)],
            f"L21 said: {LEAF_ANSWERS[LEAF21]}\nL22 said: {LEAF_ANSWERS[LEAF22]}",
        ),
        LEAF11: _leaf_file(LEAF11, leaf_prompt),
        LEAF12: _leaf_file(LEAF12, leaf_prompt),
        LEAF21: _leaf_file(LEAF21, leaf_prompt),
        LEAF22: _leaf_file(LEAF22, leaf_prompt),
        CHAIN1: _spawner_file(
            CHAIN1,
            "You are at depth 1. Recurse.",
            [(CHAIN2, "Chain depth 2", "You are at depth 2. Recurse.")],
            CHAIN1_ANSWER,
        ),
        CHAIN2: _spawner_file(
            CHAIN2,
            "You are at depth 2. Recurse.",
            [(CHAIN3, "Chain depth 3", "You are at depth 3. Stop.")],
            CHAIN2_ANSWER,
        ),
        CHAIN3: [
            _user(f"{CHAIN3}-u1", None, "You are at depth 3. Stop.", agent_id=CHAIN3),
            _assistant(
                f"{CHAIN3}-a1",
                f"{CHAIN3}-u1",
                [{"type": "text", "text": CHAIN3_ANSWER}],
                agent_id=CHAIN3,
            ),
        ],
        # The interrupted agent got to think before being killed; there is
        # no final answer and no result tail anywhere.
        INTR: [
            _user(f"{INTR}-u1", None, "Run forever.", agent_id=INTR),
            _assistant(
                f"{INTR}-a1",
                f"{INTR}-u1",
                [{"type": "thinking", "thinking": "Looping…"}],
                agent_id=INTR,
            ),
        ],
    }

    descriptions = {
        MID1: "Mid-agent 1",
        MID2: "Mid-agent 2",
        LEAF11: "Leaf 1.1",
        LEAF12: "Leaf 1.2",
        LEAF21: "Leaf 2.1",
        LEAF22: "Leaf 2.2",
        CHAIN1: "Chain depth 1",
        CHAIN2: "Chain depth 2",
        CHAIN3: "Chain depth 3",
        INTR: "Doomed spawn",
    }
    for agent_id, rows in files.items():
        _write_jsonl(subagents / f"agent-{agent_id}.jsonl", rows)
        (subagents / f"agent-{agent_id}.meta.json").write_text(
            json.dumps(
                {
                    "agentType": "general-purpose",
                    "description": descriptions[agent_id],
                    "toolUseId": _tu(agent_id),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    n_files = sum(1 for _ in FIXTURE.rglob("*") if _.is_file())
    print(f"Wrote {n_files} files under {FIXTURE}")


if __name__ == "__main__":
    main()
