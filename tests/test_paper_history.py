"""Closed Paper fills survive account updates and independent strategy versions."""
import copy

import pytest

from multiedge.dashboard import ResearchService
from multiedge.research import ResearchStore
from tests.test_daily_pnl_service import closed_account


def history():
    account = closed_account()
    second = {**account["trades"][0], "ticker": "MMM", "source_signal_id": "b"}
    account["trades"].append(second)
    account["state"]["trades"] = copy.deepcopy(account["trades"])
    return account


@pytest.mark.parametrize("change", ["delete", "price", "pnl", "reorder", "nested_delete", "omit_state"])
def test_closed_history_cannot_be_removed_or_rewritten(tmp_path, change):
    store = ResearchStore(tmp_path)
    original = store.save_paper(history())
    changed = copy.deepcopy(original)
    if change == "delete":
        changed["trades"] = []
    elif change == "price":
        changed["trades"][0]["exit_price"] += 1
    elif change == "pnl":
        changed["trades"][0]["net_pnl"] = 0
    elif change == "reorder":
        changed["trades"].reverse()
    elif change == "nested_delete":
        changed["state"]["trades"] = []
    else:
        changed.pop("state")
    with pytest.raises(ValueError, match="历史平仓"):
        store.save_paper(changed)
    assert store.get_paper(original["account_id"]) == original


def test_append_and_nonhistorical_updates_remain_allowed(tmp_path):
    store = ResearchStore(tmp_path)
    original = store.save_paper(history())
    changed = copy.deepcopy(original)
    third = {**changed["trades"][0], "ticker": "NVDA", "source_signal_id": "c"}
    changed["trades"].append(third)
    changed["state"]["trades"].append(copy.deepcopy(third))
    changed["cash"] += third["net_pnl"]
    changed["last_checked_at"] = "2026-11-18T15:05:00+00:00"
    saved = store.save_paper(changed)
    assert saved["trades"][:2] == original["trades"] and len(saved["trades"]) == 3
    assert store.save_paper(saved) == saved


def test_new_empty_version_keeps_old_history_in_workspace_and_direct_read(tmp_path):
    service = ResearchService(state_dir=tmp_path)
    original = service.store.save_paper(history())
    new = {**closed_account(), "account_id": "new-v2", "strategy_version": "structural_retest_v2",
           "trades": [], "cash": 100000, "equity": 100000}
    service.store.save_paper(new)
    accounts = {account["account_id"]: account for account in service.state()["paper_accounts"]}
    assert accounts["new-v2"]["trades"] == []
    assert accounts[original["account_id"]]["trades"] == original["trades"]
    assert service.paper_account(original["account_id"])["trades"] == original["trades"]
    assert service.store.get_paper(original["account_id"]) == original
