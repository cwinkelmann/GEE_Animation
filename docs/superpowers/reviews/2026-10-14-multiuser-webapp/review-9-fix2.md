# Re-review of Task 9 fix round 2 (7529b63)

Verdict: changes-requested (all 11 findings fixed; one test-quality finding and two minors).

Mutations actually run (edit source, run test, restore from copy):
1 FIXED: resolve()->absolute() fails symlink case; containment disabled fails; root widened to parent fails.
  Attack probe (12 variants: direct, file symlink, dir symlink, '..', upper-case path, NUL byte, cache dir itself,
  relative traversal) all refused; only a genuine cache file allowed. Case differences only make the check stricter
  (string compare after realpath). TOCTOU: the resolved path is what gets copied; closing the race would need write
  access to the upload cache, which clients only have via file uploads (no symlinks). Residual (accepted): the Gradio
  cache is shared by all users, so user B can name user A's cached upload IF B knew its hash-named path.
  Test weakness: the '..' case in the traversal test names a nonexistent file, so it is refused by is_file(), not containment.
2 FIXED: dropping limit= fails the 150 KB test; removing ValueError from the except fails the over-limit test.
  After overrun the job ends with "result too large to read back", not the start-up message.
3 FIXED: list_runs over wrong user fails.
4 FIXED: dropping max_file_size fails (413 probe).
5 FIXED: no install fails; no removal fails. Removal is in finally, by identity (own instance). Gaps: if
  _on_start raises after installing (storage.sweep), the filter leaks; if the finally's gather is itself cancelled it is skipped.
6 FIXED: removing env= fails.
7 FIXED: proc.kill() instead of killpg fails.
8 FIXED: no suffix fails; unsanitised sensor fails. Hostile '../../etc' -> '______etc', 'a b/c' -> 'a_b_c', '.hidden' -> '_hidden'.
9 FIXED: removing timeout fails.
10 FIXED, but see finding N1. Cancel removed: test HANGS (killed by my 60 s alarm; no pytest-timeout installed). Sweep-once-only fails. Payload kept fails.
11 FIXED: comment only.

Credential constraint holds: token only on stdin; worker_env scrubs GEE_OAUTH_*/GEE_COOKIE_SECRET; WorkerProcess._payload dropped
after write; new messages contain no token or path.

New findings
- N1 Important: tests/test_webapp_app.py test_the_retention_sweep_repeats... hangs CI if cancel regresses (lifespan awaits the
  uncancelled sweeper forever). Make it fail fast: run the TestClient block in a thread and join(timeout), or assert
  sweeper.cancel was called / bound the await with asyncio.wait_for in the lifespan.
- N2 Minor app.py:_sweeper: no try/except in loop; one OSError from storage.sweep ends retention silently for the rest of uptime.
- N3 Minor app.py _on_start: filter leaks if storage.sweep raises after the lastResort install.
- N4 Minor test '..' case vacuous for containment (see 1).

Suite: 834 passed, 1 skipped; tests/test_gui.py 82 passed and unmodified; pyproject untouched; no network.
Working tree clean (git status --porcelain empty). Stray atk_* temp dirs my probe created outside the worktree were removed.
