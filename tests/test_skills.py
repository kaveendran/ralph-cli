"""Skills: discovery, lint, suggestions, dependency versions, staleness,
drafting and evals; plus the complexity analysis, which uses the same kind
of read-only Claude call."""

import json
import unittest

from ralph.graph.complexity import analyze
from ralph.model.plan import parse_plan_text
from ralph.model.unit import Unit
from ralph.skills.deps import installed_versions, package_name
from ralph.skills.evals import run_evals
from ralph.skills.generate import generate_stack_skills
from ralph.skills.lint import lint_skill, lint_skills
from ralph.skills.refresh import stale_docs, stale_skills, version_matches
from ralph.skills.registry import discover
from ralph.skills.select import suggest
from ralph.skills.stats import compare
from tests.helpers import ScriptedRunner, TempDirTestCase, agent_result, result_events


def write_skill(root, name: str, description: str, body: str = "# Body\n", **meta) -> None:
    folder = root / ".claude" / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{key}: {value}\n" for key, value in meta.items())
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}")


class Registry(TempDirTestCase):
    def test_discovers_skills_with_their_paths_and_versions(self):
        write_skill(self.tmp, "api-conventions", "Writes FastAPI endpoints.", paths="[backend/**]")
        (self.tmp / ".claude" / "skills" / "api-conventions" / "SKILL.md").write_text(
            "---\nname: api-conventions\ndescription: Writes FastAPI endpoints.\npaths: [backend/**]\n"
            "ralph-versions:\n  fastapi: \"0.115\"\n---\n# API\n")
        (skill,) = discover(self.tmp)
        self.assertEqual((skill.name, skill.paths, skill.versions), ("api-conventions", ["backend/**"],
                                                                    {"fastapi": "0.115"}))


class Lint(TempDirTestCase):
    def test_follows_the_authoring_guidance(self):
        write_skill(self.tmp, "Bad_Name", "I help with things.")
        write_skill(self.tmp, "no-description", "")
        write_skill(self.tmp, "long-body", "Writes tests.", body="x\n" * 501)
        write_skill(self.tmp, "links", "Writes docs.", body="See [ref](ref.md) and [gone](gone.md).\n")
        (self.tmp / ".claude" / "skills" / "links" / "ref.md").write_text("Then [deeper](deeper.md).\n")
        found = {}
        for skill in discover(self.tmp):
            found[skill.dir.name] = [f.message for f in lint_skill(skill)]
        self.assertTrue(any("lowercase words" in m for m in found["Bad_Name"]))
        self.assertTrue(any("no `description`" in m for m in found["no-description"]))
        self.assertTrue(any("501 lines" in m for m in found["long-body"]))
        self.assertTrue(any("gone.md, which doesn't exist" in m for m in found["links"]))
        self.assertTrue(any("one level deep" in m for m in found["links"]))

    def test_a_good_skill_passes_and_too_many_skills_warn(self):
        write_skill(self.tmp, "api-conventions", "Writes FastAPI endpoints the way this project does. "
                    "Use when adding or changing an endpoint.")
        self.assertEqual(lint_skills(discover(self.tmp)), [])
        for n in range(15):
            write_skill(self.tmp, f"skill-{n}", "Does a thing.")
        self.assertIn("16 skills", lint_skills(discover(self.tmp))[-1].message)


class Suggestions(TempDirTestCase):
    def test_paths_beat_words_and_weak_matches_are_left_out(self):
        write_skill(self.tmp, "db-migrations", "Writes Alembic migrations.", paths="[backend/migrations/**]")
        write_skill(self.tmp, "api-conventions", "Writes api endpoints with routers and pydantic schemas.")
        write_skill(self.tmp, "frontend-style", "Styles React components.")
        unit = Unit("U001", "Invoice endpoint", "cr1", type="api",
                    key_files=["backend/migrations/0003_invoices.py", "backend/api/invoices.py"])
        picked = [(s.name, why) for s, why in suggest(discover(self.tmp), unit, "Add routers and pydantic schemas")]
        self.assertEqual([name for name, _ in picked], ["db-migrations", "api-conventions"])
        self.assertIn("paths match", picked[0][1])
        self.assertIn("api work", picked[1][1])


class Versions(TempDirTestCase):
    def test_lock_files_win_over_manifests_and_subfolders_are_read(self):
        (self.tmp / "pyproject.toml").write_text('[project]\ndependencies = ["FastAPI>=0.110", "pydantic[email]~=2.7"]\n')
        (self.tmp / "uv.lock").write_text('[[package]]\nname = "fastapi"\nversion = "0.115.4"\n')
        frontend = self.tmp / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text(json.dumps({"dependencies": {"react": "^19.0.0", "zod": "3.23.8"}}))
        (frontend / "package-lock.json").write_text(json.dumps({"packages": {
            "": {}, "node_modules/react": {"version": "19.1.0"},
            "node_modules/a/node_modules/react": {"version": "17.0.0"}}}))
        versions = installed_versions(self.tmp)
        self.assertEqual(versions["fastapi"], ("0.115.4", "uv.lock"))
        self.assertEqual(versions["pydantic"][0], "2.7")
        self.assertEqual(versions["react"], ("19.1.0", "frontend/package-lock.json"))
        self.assertEqual(versions["zod"][0], "3.23.8")
        self.assertEqual(package_name("Typing_Extensions[extra]"), "typing-extensions")

    def test_a_pin_matches_at_a_dot_boundary(self):
        self.assertTrue(version_matches("0.115", "0.115.4"))
        self.assertTrue(version_matches("v19", "19.1.0"))
        self.assertFalse(version_matches("0.115", "0.1150.0"))
        self.assertFalse(version_matches("0.115", "0.116.0"))

    def test_stale_skills_and_docs_are_flagged_after_an_upgrade(self):
        (self.tmp / "requirements.txt").write_text("fastapi==0.116.1\nhttpx==0.27.0\n")
        (self.tmp / ".claude" / "skills" / "api").mkdir(parents=True)
        (self.tmp / ".claude" / "skills" / "api" / "SKILL.md").write_text(
            "---\nname: api\ndescription: x\nralph-versions:\n  fastapi: \"0.115\"\n  httpx: \"0.27\"\n---\n")
        (self.tmp / "docs" / "vendor" / "httpx@0.26").mkdir(parents=True)
        versions = installed_versions(self.tmp)
        stale = stale_skills(discover(self.tmp), versions) + stale_docs(self.tmp, versions)
        self.assertEqual([(s.item, s.package, s.installed) for s in stale],
                         [("api", "fastapi", "0.116.1"), ("docs/vendor/httpx@0.26", "httpx", "0.27.0")])


def structured_step(structured: dict):
    return lambda call: agent_result(call, result_events(structured))


class Drafting(TempDirTestCase):
    def test_drafts_are_written_with_their_versions_and_never_overwrite(self):
        write_skill(self.tmp, "react-conventions", "Mine.")
        runner = ScriptedRunner(structured_step({"skills": [
            {"name": "FastAPI Conventions", "description": "Writes endpoints. Use when adding one.",
             "body": "# FastAPI\n\nDecide: sync or async?", "packages": ["fastapi", "left-pad"]},
            {"name": "react-conventions", "description": "Theirs.", "body": "# React"},
            {"name": "!!!", "description": "x", "body": "x"},
        ]}))
        written = generate_stack_skills(runner, self.tmp, "FastAPI + React", {"fastapi": ("0.115.4", "uv.lock")},
                                        self.tmp / "run")
        self.assertEqual([p.parent.name for p in written], ["fastapi-conventions"])
        (skill,) = [s for s in discover(self.tmp) if s.name == "fastapi-conventions"]
        self.assertEqual(skill.versions, {"fastapi": "0.115"})
        self.assertIn("Review it before relying on it", skill.body)
        self.assertEqual(lint_skill(skill), [])
        self.assertIn("Mine.", (self.tmp / ".claude/skills/react-conventions/SKILL.md").read_text())
        call = runner.calls[0]
        self.assertEqual(call.tools, [])
        self.assertIn("fastapi 0.115.4", call.prompt)


class Evals(TempDirTestCase):
    def test_a_scenario_passes_when_the_skill_triggers_and_the_answer_has_what_it_expects(self):
        write_skill(self.tmp, "api-conventions", "Writes endpoints.")
        evals = self.tmp / ".claude" / "skills" / "api-conventions" / "evals"
        evals.mkdir()
        (evals / "endpoint.json").write_text(json.dumps({"prompt": "Add an endpoint", "expect": ["router", "Pydantic"]}))
        (evals / "unrelated.json").write_text(json.dumps({"prompt": "Fix the CSS", "should_trigger": False}))

        def triggered(call):
            return agent_result(call, [
                {"type": "assistant", "message": {"id": "m", "content": [
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "api-conventions"}}]}},
                {"type": "result", "subtype": "success", "result": "Use a router.", "total_cost_usd": 0.01}])

        runner = ScriptedRunner(triggered, triggered)
        (skill,) = discover(self.tmp)
        results = {r.scenario: r for r in run_evals(runner, skill, self.tmp, self.tmp / "run")}
        self.assertEqual(results["endpoint"].missing, ["Pydantic"])
        self.assertFalse(results["unrelated"].passed)          # it triggered when it shouldn't
        self.assertIn("Skill", runner.calls[0].tools)
        self.assertEqual(runner.calls[0].model, "haiku")


class Complexity(TempDirTestCase):
    def test_scores_known_units_and_keeps_splits(self):
        plan = parse_plan_text("## Unit: Big (ID: U001)\nLots.\n\n## Unit: Small (ID: U002)\nLittle.\n",
                               self.tmp / "plan.md")
        units = {"U001": Unit("U001", "Big", "cr1"), "U002": Unit("U002", "Small", "cr1")}
        runner = ScriptedRunner(structured_step({"units": [
            {"id": "U001", "score": 5, "reason": "three subsystems", "split": ["schema", "endpoint"]},
            {"id": "U002", "score": 1, "reason": "one file"},
            {"id": "U999", "score": 3, "reason": "not a unit"}]}))
        scores = analyze(runner, self.tmp, plan, units, self.tmp / "run")
        self.assertEqual([(s.unit_id, s.too_complex, s.split) for s in scores],
                         [("U001", True, ["schema", "endpoint"]), ("U002", False, [])])
        self.assertIn("### U001 — Big", runner.calls[0].prompt)
        self.assertEqual(runner.calls[0].tools, [])



class Effect(unittest.TestCase):
    def test_iterations_with_a_skill_are_compared_with_the_rest(self):
        def record(unit, outcome, turns, cost, skills=()):
            return {"unit": unit, "outcome": outcome,
                    "agent": {"num_turns": turns, "total_cost_usd": cost, "skills_invoked": list(skills)}}
        records = [record("U1", "done", 10, 1.0, ["api"]), record("U2", "done", 20, 2.0, ["api"]),
                   record("U3", "retry", 40, 4.0), record("U3", "done", 30, 3.0), record("task", "done", 5, 0.5)]
        (effect,) = compare(records)
        self.assertEqual((effect.skill, effect.used.iterations, effect.used.turns, effect.used.done), ("api", 2, 15, 1.0))
        self.assertEqual((effect.unused.iterations, effect.unused.cost_usd, effect.unused.done), (2, 3.5, 0.5))
        self.assertEqual(compare([record("U1", "done", 1, 0)]), [])


if __name__ == "__main__":
    unittest.main()
