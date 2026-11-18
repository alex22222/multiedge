# Multi-Edge VPS deployment

The authorized target is **173.199.122.23**. The deployment script has no host override. The PolyHermes/polymtrade execution server **107.191.42.75 is excluded**.

The dashboard uses `https://spaceflag.site/multiedge/` and the existing Binance dashboard login. Caddy asks the authenticated Binance `/health` endpoint on port 4173 before forwarding a request. The application itself binds only to `127.0.0.1:8321`; Caddy strips `/multiedge` and supplies `X-Forwarded-Prefix: /multiedge`.

## Files and resource limits

| Item | Location |
| --- | --- |
| Releases and a separate venv per release | `/opt/multiedge/releases/<UTC>-<commit>/` |
| Active release symlink | `/opt/multiedge/current` |
| Database, data cache and import audit files | `/var/lib/multiedge/` |
| Dedicated Unix account | `multiedge` |
| Dashboard | `multiedge.service`, 180 MB / 20% CPU |
| Daily research job | `multiedge-daily.service`, 160 MB / 25% CPU |
| Intraday Paper checks | `multiedge-paper-monitor.service`, 128 MB / 20% CPU |

All services run with a read-only filesystem except for their own state directory. OpenBLAS and OMP use one thread. The scheduled intraday and daily jobs use different hours; the daily job performs its own close reconciliation within its 160 MB limit. Deployment does not copy Multica CLI authentication, `.env` files, keys, local discussion context, Git metadata or runtime databases. It does not change Binance or GMGN application units or trading configuration.

Prerequisites on the VPS: Python 3.10+ with `venv`, `pip`, `curl`, systemd, Caddy and the existing `/gmgn/` shared-login route. On the Mac, the existing SSH identity is `~/.ssh/polymtrade_vultr_ed25519`; SSH host-key verification remains enabled.

## Deploy

Run the tests and review the working tree before packaging. The script deploys the current working tree, including uncommitted source changes; its release suffix records the base Git commit.

```bash
bash deploy/deploy-vps.sh

# Optional: import a sanitized research-run JSON through jobs.import-run.
bash deploy/deploy-vps.sh --run-json /absolute/path/to/sanitized-run.json
```

Use only a validated run JSON prepared for `multiedge.jobs import-run`. Do not pass a raw issue export, comments, execution logs, CLI settings or token-bearing/signed URLs. The imported JSON is retained under `/var/lib/multiedge/imports/` as an audit record. Multica task status and actual data coverage must remain explicit; five detail attachments do not constitute the full 101-stock result table.

The script checks the target's login gate, packages source, creates an isolated release and venv, runs the full tests, seeds existing cached examples, optionally imports the sanitized run, then atomically switches the active symlink. It backs up the existing units and Caddyfile, adds only its own marked Caddy block, validates before reloading, and checks loopback health plus the unauthenticated public redirect. On failure after activation it restores the previous symlink, units and Caddyfile. State and release directories are retained for inspection; state changes are not rolled back.

Finish verification in a logged-in browser: open the candidate list, one imported stock, its evidence and validation notes, an example strategy HTML report, Paper results and an indicator experiment. A login redirect alone does not verify these pages.

## Daily schedule

The timer is installed but **is not enabled by default**. An already enabled timer retains its state on an update. Enable it after the full data/universe route and one manual daily run are verified:

```bash
sudo systemctl start multiedge-daily.service
sudo journalctl -u multiedge-daily.service --no-pager -n 80
sudo systemctl enable --now multiedge-daily.timer
sudo systemctl list-timers multiedge-daily.timer
```

`--enable-daily` performs the last activation step during deployment when those checks have already passed. The schedule is Monday–Friday at **18:30 America/New_York**, following U.S. daylight saving time. `Persistent=true` catches up a missed timer. The jobs layer must choose a completed session, handle market holidays and persist idempotent progress; the timer itself is not an exchange calendar. It performs research and Paper validation only.

The corrected default registers every baseline BUY in a structural-exit account, then reconciles its completed 5-minute bars. Initial stop/target levels come from the frozen signal report, rather than universal 3%/5% stops or a 25% target. The existing engine selects a structural stop using its MA/trendline/E1-anchor candidates and project-specific 0.1×ATR buffer; the buffer is an operational assumption, not an exact video prescription. Each risk plan retains its source report and anchor/date evidence, then computes risk distance, share quantity and RR from the actual simulated entry price. With no valid prior-high target, the account tracks the stop only and leaves target/RR unavailable.

Fixed percentages remain optional, explicitly chosen experiments. The two previously automatic 3%/25% and 5%/25% example accounts must retain their original orders, fills and levels, with `automation_disabled=True` making them read-only and excluded from both daily enrollment and price-check advancement. A new structural account starts at actual registration; do not rewrite old trades or backfill new ones. Existing structural daily/replay accounts remain independent.

The `5751f20`/195-test receipt describes the earlier fixed-example release, while `9950072`/216 tests records the subsequent structural-default correction. Preserve those as historical evidence. The new trailing-stop feature requires its own deployment, actual activation, browser and runtime receipts; earlier results do not prove that this feature is live.

## Five-minute Paper schedule

After one manual price check succeeds, enable the separate timer:

```bash
sudo systemctl start multiedge-paper-monitor.service
sudo journalctl -u multiedge-paper-monitor.service --no-pager -n 40
sudo systemctl enable --now multiedge-paper-monitor.timer
sudo systemctl list-timers multiedge-paper-monitor.timer
```

`--enable-monitor` can activate it during later verified deployments. It runs every five minutes from 09:30 through 16:05 New York time on weekdays. `Persistent=false` avoids interpreting missed timer ticks as past executions; each successful check reconciles available completed bars after actual registration. Actual sessions, holidays, early closes and source freshness are checked against provider session metadata. Missing, stale or discontinuous quotes pause affected processing and appear in `last-paper-monitor.json` and account records. Normalized bars and source hashes are retained under `/var/lib/multiedge/intraday_quotes`; source exchange delay may be unknown. This is OHLC-based Paper simulation, with no brokerage connection.

Five-minute checks continue to execute saved protection. The new default for active structural 5-minute accounts also evaluates the `structure_ma20_hl_v1` trailing policy from completed daily data; it never lowers a stop. Continue the existing 18:30 New York daily close reconciliation and the disabled status of the two historical example accounts.

## Trailing-stop activation and audit

The source principles are the video's 20MA-with-buffer exit guidance and stronger-price structures (F2b, F4, G1/G2). The following are explicit project assumptions, not a verbatim video algorithm:

- Treat source session D as complete only after **16:30 America/New_York**, and truncate indicator/pivot inputs to D. Compute the lower edge of 20SMA/20EMA minus 0.1×ATR(14). A rising band with the close still supporting it is eligible; a close already below the buffered protection can also register a future exit level.
- A structural candidate uses a confirmed higher pivot low whose date is strictly after entry, with three complete right-hand sessions confirming it. Subtract the same ATR buffer. Floor candidates to cents; choose the higher eligible MA or structure candidate and require an increase of at least $0.01. This tighter OR protection may exit earlier than waiting for confluence; it never widens the stop.
- Register each proposal with its actual observation and policy activation times. Apply it only in a later session than the source day and on a qualified bar beginning no earlier than registration/activation. Normally that is the next actual session open; late registration waits for a subsequent eligible bar. If that open is below the new protection, exit at the actual open with configured slippage, not at a fictional historical close or stop price.
- Retain `initial_stop` and `initial_risk_per_share`. Planned RR and realized R continue to use original price-distance risk; net PnL includes fees. The original target remains unchanged, including an unavailable target.

New active structural 5-minute accounts receive the versioned policy. Existing open structural holdings activate at the actual upgrade/check time, with an appended policy event; do not backdate activation, rewrite purchases, or recalculate closed trades. Fixed-percentage, disabled, daily and replay accounts keep their existing behavior. Holdings are managed even after they cease to be BUY signals.

Daily prices are obtained from the account's verified provider/cache independently of the scanner's current BUY list. Failed freshness, hash, market-calendar or corporate-action checks pause only new trailing calculations and are reported in `monitor.trailing_errors`; valid existing protective orders continue to run on valid 5-minute data. Missing/discontinuous intraday bars still invoke the separate execution-coverage protections.

Inspect `trailing_policy`, `stop_updates` and `stop_adjustments` in the Paper account. `pending` is a proposal, not an applied change; `applied`, `no_change` and `position_closed` distinguish outcomes. An applied record must retain old/new prices, source day and completion time, actual observation, requested and actual effective times, processing time, indicator/confirmed-pivot evidence, source fingerprints and policy version. The dashboard shows initial/current stops and the full adjustment audit; zero adjustments must remain explicit when no raise has become effective.

Before recording acceptance, back up persistent state, verify actual activation of existing open structural holdings, preservation of original risk/history and the disabled examples, then check one real monitor run, both timers, the source/proposal/application distinctions and browser evidence. Release `67b6b6b` passed 262 tests locally and on the VPS; its live activation, two prospective stop adjustments, actual-open Paper exits and independent accounting/browser checks are recorded in `docs/2026-11-18-DEPLOYMENT.md` (fourth iteration).

## Inspect and roll back

```bash
sudo systemctl status multiedge multiedge-daily.timer multiedge-paper-monitor.timer
sudo journalctl -u multiedge -u multiedge-daily --no-pager -n 80
curl --fail http://127.0.0.1:8321/health
readlink -f /opt/multiedge/current
```

For a manual rollback, select a retained release and switch `/opt/multiedge/current` using a temporary symlink plus `mv -Tf`, then restart `multiedge`. The newer release contains `Caddyfile.before` and `.previous-units/` if the route or units also need restoring. Validate the restored Caddyfile before reloading. Keep `/var/lib/multiedge` in place; a code rollback does not reverse database migrations or erase research/Paper history. No release pruning or runtime-state deletion is automated.
