"""Tools that Fred and the workers can call besides the built-in ones (files, shell, web).
Each tool that needs the user (a secret, a folder, an approval of the plan) shows a card in the chat and
waits for the answer."""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from claude_agent_sdk import create_sdk_mcp_server

if TYPE_CHECKING:
    from .session import Session

from . import tools_building, tools_common, tools_delivery, tools_planning, tools_setup

# used through this module by the engine, the tracking and the tests
add_test_result = tools_common.add_test_result
load_tests = tools_common.load_tests
tests_file = tools_common.tests_file
diagrams_on = tools_common.diagrams_on
interrupted = tools_common.interrupted
diagram_hint = tools_common.diagram_hint


def build_server(session: "Session"):
    t = SimpleNamespace(**tools_setup.make(session), **tools_planning.make(session), **tools_building.make(session),
                        **tools_delivery.make(session))
    setup_tools = [t.check_environment, t.save_setting, t.request_secret, t.ask_folder, t.ask_choice, t.github_check, t.finish_setup,
                   t.connect_phone]
    project_tools = [t.check_environment, t.save_setting, t.request_secret, t.ask_choice, t.github_check, t.github_create_repo,
                     t.github_push, t.request_plan_approval, t.take_screenshot, t.show_image, t.launch_for_user,
                     t.record_test_result, t.start_iteration, t.commit_iteration]
    project_tools += [t.handoff_to_architect, t.complete_task, t.record_red_tests, t.record_prior_art, t.ask_council,
                      t.record_change_request, t.run_security_test, t.record_finding, t.close_finding, t.clean_leftovers,
                      t.record_problem, t.record_challenge_review, t.record_diagram, t.record_questions, t.run_tests,
                      t.record_lesson, t.quality_check, t.package_app, t.add_ci, t.github_release]
    architect_tools = [t.check_environment, t.ask_choice, t.request_architecture_approval, t.return_to_fred,
                       t.ask_council]
    chosen = setup_tools if session.kind == "setup" else (architect_tools if session.persona == "Mat" else project_tools)
    return create_sdk_mcp_server(name="aiteam", version="1.0.0", tools=chosen), chosen
