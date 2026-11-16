"""Direct-mode tests for MotionDesk.

    env -u PYTHONPATH -u VIRTUAL_ENV -u PYTHONHOME uv run --python 3.13 \
        --with pytest --with genlayer-test pytest tests/direct -q

The motion bodies and the model are mocked; the contract is not. Time is moved
with direct_vm.warp(), so the voting window and the timelock are exercised
without waiting.
"""

import datetime as _dt
import json

CONTRACT = "contracts/motion_desk.py"
PROMPT = r"structuring a governance motion"

TARGET = "0x" + "ab" * 20
WINDOW = 3600
TIMELOCK = 600

T0 = "2026-01-01T00:00:00Z"
T0_PLUS_HOUR = "2026-01-01T01:00:00Z"
T0_PLUS_70_MIN = "2026-01-01T01:10:00Z"
T0_SECONDS = int(_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc).timestamp())
T70_SECONDS = int(_dt.datetime(2026, 1, 1, 1, 10, tzinfo=_dt.timezone.utc).timestamp())


def who(addr):
    from genlayer import Address

    return str(Address(bytes(addr))).lower()


def raw(who_addr):
    return bytes.fromhex(who_addr[2:])


def pay(target=TARGET, value=100, params=None, summary="Pay 100 to the gardener"):
    return json.dumps({
        "action_type": "PAY", "target": target, "value": value,
        "params": params or {}, "summary": summary, "ambiguous": False,
    })


def deploy(direct_deploy):
    return direct_deploy(CONTRACT)


def mock_round(direct_vm, payload):
    direct_vm.clear_mocks()
    direct_vm.mock_llm(PROMPT, payload if isinstance(payload, str) else json.dumps(payload))


def submit(contract, direct_vm, who_addr, body="Hire a gardener for the common garden", window=WINDOW, timelock=TIMELOCK):
    direct_vm.sender = who_addr
    return contract.submit_motion(body, window, timelock)


def structure(contract, direct_vm, who_addr, motion_id=1, payload=None):
    mock_round(direct_vm, payload if payload is not None else pay())
    direct_vm.sender = who_addr
    return contract.structure(motion_id)


def to_voting(contract, direct_vm, who_addr, motion_id=1, payload=None, window=WINDOW, timelock=TIMELOCK):
    submit(contract, direct_vm, who_addr, window=window, timelock=timelock)
    return structure(contract, direct_vm, who_addr, motion_id, payload)


def vote(contract, direct_vm, who_addr, choice, motion_id=1):
    direct_vm.sender = who_addr
    return contract.vote(motion_id, choice)


class TestSubmit:
    def test_a_motion_starts_as_a_draft(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)

        out = submit(contract, direct_vm, direct_alice)
        assert out["id"] == 1
        assert out["author"] == who(direct_alice)
        assert out["status"] == "draft"
        assert out["vote_window"] == WINDOW
        assert out["timelock"] == TIMELOCK
        assert out["voting_ends"] == 0 and out["execute_after"] == 0

        motion = contract.get_motion(1)
        assert motion["body"].startswith("Hire a gardener")
        assert motion["action_type"] == "" and motion["target"] == ""
        assert motion["value"] == 0 and motion["summary"] == "" and motion["reason"] == ""
        assert motion["yes"] == 0 and motion["no"] == 0 and motion["abstain"] == 0
        assert motion["created_at"] == T0_SECONDS

        assert contract.stats() == {
            "motions": 1, "voting": 0, "passed": 0, "failed": 0, "executed": 0, "votes": 0,
        }

    def test_ids_count_from_one(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        assert submit(contract, direct_vm, direct_alice)["id"] == 1
        assert submit(contract, direct_vm, direct_alice, body="Second motion")["id"] == 2

    def test_the_body_and_the_windows_are_bounded(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)

        with direct_vm.expect_revert("a motion needs a body"):
            submit(contract, direct_vm, direct_alice, body="   ")
        with direct_vm.expect_revert("at most 4000 characters"):
            submit(contract, direct_vm, direct_alice, body="x" * 4001)
        with direct_vm.expect_revert("between 1 second and 30 days"):
            submit(contract, direct_vm, direct_alice, window=0)
        with direct_vm.expect_revert("between 1 second and 30 days"):
            submit(contract, direct_vm, direct_alice, timelock=30 * 24 * 60 * 60 + 1)

        day = 24 * 60 * 60
        out = submit(contract, direct_vm, direct_alice, body="A motion at the cap", window=30 * day, timelock=30 * day)
        assert out["vote_window"] == 30 * day


class TestStructure:
    def test_structure_turns_prose_into_a_pay_action(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        out = structure(contract, direct_vm, direct_alice)
        assert out["status"] == "voting"
        assert out["action_type"] == "PAY"
        assert out["target"] == TARGET
        assert out["value"] == 100
        assert out["params"] == {}
        assert out["summary"] == "Pay 100 to the gardener"
        assert out["reason"] == ""
        assert out["voting_ends"] == T0_SECONDS + WINDOW
        assert out["execute_after"] == T0_SECONDS + WINDOW + TIMELOCK

    def test_structure_keeps_call_params(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)
        payload = {
            "action_type": "CALL", "target": TARGET, "value": 0,
            "params": {"cycle": "weekly", "action": "water"},
            "summary": "Call the garden contract", "ambiguous": False,
        }
        out = structure(contract, direct_vm, direct_alice, payload=payload)
        assert out["action_type"] == "CALL"
        assert out["params"] == {"cycle": "weekly", "action": "water"}
        assert out["target"] == TARGET

    def test_structure_refuses_an_unknown_action_type(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        mock_round(direct_vm, json.dumps({
            "action_type": "BURN", "target": "", "value": 0,
            "params": {}, "summary": "Burn the budget", "ambiguous": False,
        }))
        direct_vm.sender = direct_alice
        out = contract.structure(1)
        assert out["status"] == "needs_revision"
        assert "not one of PAY, CALL or TEXT" in out["reason"]
        assert out["action_type"] == "" and out["value"] == 0
        assert contract.get_motion(1)["voting_ends"] == 0

    def test_structure_refuses_broken_json(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        out = structure(contract, direct_vm, direct_alice, payload="{not json")
        assert out["status"] == "needs_revision"
        assert "did not return JSON" in out["reason"]

    def test_structure_fails_closed_on_an_ambiguous_reading(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        mock_round(direct_vm, json.dumps({
            "action_type": "", "target": "", "value": 0,
            "params": {}, "summary": "", "ambiguous": True,
        }))
        direct_vm.sender = direct_alice
        out = contract.structure(1)
        assert out["status"] == "needs_revision"
        assert "could not read this motion as one action" in out["reason"]
        assert out["action_type"] == "" and out["summary"] == ""

    def test_structure_refuses_a_target_that_is_not_an_address(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        out = structure(contract, direct_vm, direct_alice, payload=pay(target="0x1234"))
        assert out["status"] == "needs_revision"
        assert "is not an address" in out["reason"]

        mock_round(direct_vm, pay(target="not-an-address"))
        direct_vm.sender = direct_alice
        out = contract.structure(1)
        assert out["status"] == "needs_revision"
        assert "is not an address" in out["reason"]

    def test_structure_refuses_a_negative_value_and_a_long_summary(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        out = structure(contract, direct_vm, direct_alice, payload=pay(value=-5))
        assert out["status"] == "needs_revision"
        assert "whole number of zero or more" in out["reason"]

        mock_round(direct_vm, pay(summary="s" * 281))
        direct_vm.sender = direct_alice
        out = contract.structure(1)
        assert out["status"] == "needs_revision"
        assert "longer than 280 characters" in out["reason"]

    def test_a_refused_reading_is_revised_and_restructured(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice, body="Do everything at once")
        out = structure(contract, direct_vm, direct_alice, payload="{not json")
        assert out["status"] == "needs_revision"

        direct_vm.sender = direct_alice
        revised = contract.revise(1, "Pay 100 to the gardener")
        assert revised["status"] == "draft"
        assert revised["body"] == "Pay 100 to the gardener"
        assert revised["reason"] == ""

        out = structure(contract, direct_vm, direct_alice)
        assert out["status"] == "voting"
        assert out["action_type"] == "PAY"

    def test_only_the_author_revises_and_only_before_the_vote(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)
        structure(contract, direct_vm, direct_alice, payload="{not json")

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("only the author revises"):
            contract.revise(1, "Bob rewrites the motion")

        structure(contract, direct_vm, direct_alice)
        with direct_vm.expect_revert("cannot be revised"):
            contract.revise(1, "too late")

    def test_structure_runs_once(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("not waiting to be structured"):
            structure(contract, direct_vm, direct_alice)


class TestVoting:
    def test_yes_no_abstain_are_counted(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)

        vote(contract, direct_vm, direct_alice, "yes")
        vote(contract, direct_vm, direct_bob, "no")
        out = vote(contract, direct_vm, direct_charlie, "abstain")

        assert out["yes"] == 1 and out["no"] == 1 and out["abstain"] == 1
        assert contract.get_vote(1, who(direct_alice)) == "yes"
        assert contract.get_vote(1, who(direct_bob)) == "no"
        assert contract.get_vote(1, who(direct_charlie)) == "abstain"
        assert contract.stats()["votes"] == 3

    def test_a_revote_flips_the_counters(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)

        vote(contract, direct_vm, direct_alice, "yes")
        vote(contract, direct_vm, direct_bob, "yes")
        assert contract.get_motion(1)["yes"] == 2

        out = vote(contract, direct_vm, direct_alice, "no")
        assert out["yes"] == 1 and out["no"] == 1
        assert contract.get_vote(1, who(direct_alice)) == "no"

    def test_a_vote_after_the_window_is_refused(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)

        vote(contract, direct_vm, direct_alice, "yes")
        direct_vm.warp(T0_PLUS_HOUR)
        with direct_vm.expect_revert("the voting window has closed"):
            vote(contract, direct_vm, direct_bob, "no")
        assert contract.get_motion(1)["no"] == 0

    def test_a_vote_outside_the_voting_phase_is_refused(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        submit(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("not open for votes"):
            vote(contract, direct_vm, direct_alice, "yes")

        structure(contract, direct_vm, direct_alice)
        with direct_vm.expect_revert("a vote is yes, no or abstain"):
            vote(contract, direct_vm, direct_alice, "maybe")


class TestDelegation:
    def test_a_delegated_vote_counts_once_under_the_identity(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_alice
        out = contract.delegate(who(direct_bob))
        assert out["identity"] == who(direct_bob)
        assert contract.identity_of(who(direct_alice)) == who(direct_bob)

        vote(contract, direct_vm, direct_bob, "yes")
        assert contract.get_motion(1)["yes"] == 1

        out = vote(contract, direct_vm, direct_alice, "no")
        assert out["yes"] == 0 and out["no"] == 1
        assert contract.get_vote(1, who(direct_bob)) == "no"
        assert contract.get_vote(1, who(direct_alice)) == ""
        assert contract.stats()["votes"] == 1

    def test_a_delegation_cycle_terminates(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)

        direct_vm.sender = direct_alice
        contract.delegate(who(direct_bob))
        direct_vm.sender = direct_bob
        contract.delegate(who(direct_alice))

        assert contract.identity_of(who(direct_alice)) == who(direct_alice)
        assert contract.resolve(who(direct_bob)) == who(direct_bob)

        direct_vm.sender = direct_alice
        contract.delegate(who(direct_alice))
        assert contract.identity_of(who(direct_alice)) == who(direct_alice)

    def test_a_chain_longer_than_five_hops_falls_back_to_the_sender(self, direct_vm, direct_deploy, direct_accounts):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        accounts = [who(addr) for addr in direct_accounts[:8]]

        for index in range(5):
            direct_vm.sender = raw(accounts[index])
            contract.delegate(accounts[index + 1])
        assert contract.identity_of(accounts[0]) == accounts[5]

        direct_vm.sender = raw(accounts[5])
        contract.delegate(accounts[6])
        assert contract.identity_of(accounts[0]) == accounts[0]

    def test_a_delegation_is_cleared_with_an_empty_string(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)

        direct_vm.sender = direct_alice
        contract.delegate(who(direct_bob))
        assert contract.identity_of(who(direct_alice)) == who(direct_bob)

        direct_vm.sender = direct_alice
        contract.delegate("")
        assert contract.identity_of(who(direct_alice)) == who(direct_alice)

        with direct_vm.expect_revert("is not an address"):
            contract.delegate("bob")

    def test_two_delegated_voters_still_count_two(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_charlie
        contract.delegate(who(direct_bob))

        vote(contract, direct_vm, direct_bob, "yes")
        vote(contract, direct_vm, direct_charlie, "yes")
        vote(contract, direct_vm, direct_alice, "no")

        assert contract.get_motion(1)["yes"] == 1
        assert contract.get_motion(1)["no"] == 1


class TestTally:
    def test_tally_below_the_quorum_fails(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        vote(contract, direct_vm, direct_alice, "yes")
        vote(contract, direct_vm, direct_bob, "yes")

        direct_vm.warp(T0_PLUS_HOUR)
        out = contract.tally(1)
        assert out["status"] == "failed" and out["passed"] is False
        assert out["turnout"] == 2 and out["quorum"] == 3
        assert contract.get_motion(1)["status"] == "failed"

    def test_tally_with_quorum_and_a_yes_majority_passes(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        vote(contract, direct_vm, direct_alice, "yes")
        vote(contract, direct_vm, direct_bob, "yes")
        vote(contract, direct_vm, direct_charlie, "abstain")

        direct_vm.warp(T0_PLUS_HOUR)
        out = contract.tally(1)
        assert out["status"] == "passed" and out["passed"] is True
        assert out["turnout"] == 3 and out["yes"] == 2 and out["no"] == 0

    def test_tally_with_yes_level_with_no_fails(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_accounts):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        accounts = direct_accounts
        vote(contract, direct_vm, direct_alice, "yes")
        vote(contract, direct_vm, direct_bob, "yes")
        vote(contract, direct_vm, accounts[2], "no")
        vote(contract, direct_vm, accounts[3], "no")

        direct_vm.warp(T0_PLUS_HOUR)
        out = contract.tally(1)
        assert out["status"] == "failed" and out["passed"] is False
        assert out["turnout"] == 4 and out["yes"] == 2 and out["no"] == 2

    def test_tally_before_the_window_closes_is_refused(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        vote(contract, direct_vm, direct_alice, "yes")

        with direct_vm.expect_revert("the voting window is still open"):
            contract.tally(1)

    def test_tally_twice_is_refused(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        for who_addr in (direct_alice, direct_bob, direct_charlie):
            vote(contract, direct_vm, who_addr, "yes")
        direct_vm.warp(T0_PLUS_HOUR)
        assert contract.tally(1)["status"] == "passed"

        with direct_vm.expect_revert("cannot be tallied"):
            contract.tally(1)


class TestExecute:
    def passed(self, contract, direct_vm, direct_alice, direct_bob, direct_charlie):
        to_voting(contract, direct_vm, direct_alice)
        for who_addr in (direct_alice, direct_bob, direct_charlie):
            vote(contract, direct_vm, who_addr, "yes")
        direct_vm.warp(T0_PLUS_HOUR)
        assert contract.tally(1)["status"] == "passed"

    def test_execute_before_the_timelock_is_refused(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        self.passed(contract, direct_vm, direct_alice, direct_bob, direct_charlie)

        with direct_vm.expect_revert("the timelock has not expired"):
            contract.execute(1)

    def test_execute_after_the_timelock_runs_once(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        self.passed(contract, direct_vm, direct_alice, direct_bob, direct_charlie)

        direct_vm.warp(T0_PLUS_70_MIN)
        out = contract.execute(1)
        assert out["status"] == "executed"
        assert out["executed_at"] == T70_SECONDS

        with direct_vm.expect_revert("only a passed motion is executed"):
            contract.execute(1)

    def test_a_failed_motion_is_never_executed(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        vote(contract, direct_vm, direct_alice, "no")
        vote(contract, direct_vm, direct_bob, "no")
        direct_vm.warp(T0_PLUS_HOUR)
        assert contract.tally(1)["status"] == "failed"

        direct_vm.warp(T0_PLUS_70_MIN)
        with direct_vm.expect_revert("only a passed motion is executed"):
            contract.execute(1)


class TestViews:
    def test_the_views_read_the_motion_back(self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        to_voting(contract, direct_vm, direct_alice)
        submit(contract, direct_vm, direct_bob, body="Plant tulips along the north path")
        structure(contract, direct_vm, direct_bob, motion_id=2)

        listed = contract.list_motions(0, 10)
        assert [m["id"] for m in listed] == [2, 1]
        assert listed[1]["author"] == who(direct_alice)

        page = contract.list_motions(1, 1)
        assert [m["id"] for m in page] == [1]

        vote(contract, direct_vm, direct_charlie, "yes")
        assert contract.get_vote(1, who(direct_charlie)) == "yes"
        assert contract.get_vote(1, who(direct_alice)) == ""
        assert contract.identity_of(who(direct_charlie)) == who(direct_charlie)
        assert contract.stats()["votes"] == 1
        assert contract.stats()["voting"] == 2

    def test_an_unknown_motion_is_refused(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.warp(T0)
        with direct_vm.expect_revert("no such motion"):
            contract.get_motion(99)
