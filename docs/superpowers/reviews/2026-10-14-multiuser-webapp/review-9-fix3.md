# Review 9 fix3
Verdict: all-addressed. See final message. Verified empirically on Python 3.11.15:
- wait_for against a CancelledError-swallowing task blocks until the task ends (7.9 s for an 8 s task; forever for a truly wedged one). asyncio.wait({t}, timeout=0.5) returned in 0.5 s with the task pending. Implementer's correction is right.
- Mutations run: N1 (remove cancel -> periodic test fails by assertion, no hang; timeout=None -> bounded test fails), N2 (except ZeroDivisionError -> fails), N3 (no removal -> 2 fail; removal only if startup completed -> leak test fails), N4 (absolute() instead of resolve() -> symlink and dotdot fail, direct passes correctly).
- Suite: 839 passed, 1 skipped, 2 deselected, 1 warning. No "Task was destroyed"/never awaited output with -s.
