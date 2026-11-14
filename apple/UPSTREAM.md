# Изменения для upstream (verilator/verilator)

Подготовлены две независимые ветки от `master` (`ade77b9ca`). Каждая — один коммит и один PR.
По правилам `AGENTS.md` PR открывает человек: отредактируйте текст под себя и создайте PR вручную.

Перед отправкой:

1. `git push origin upstream-perf-cores-warning upstream-docs-clang-pgo`.
1. Создать PR из форка в `verilator/verilator:master`.
1. Если вы ещё не в `docs/CONTRIBUTORS`, добавьте себя туда сами: агентам этот файл править нельзя,
   а CI (`t_dist_contributors`) требует запись для автора коммита.
1. Полная регрессия (`make test`) локально не прогонялась: прошли новые и соседние тесты
   (`t_threads_*`) и dist-тесты стиля. Её прогонит CI.

Похожие обсуждения: закрытый PR #6839 (affinity и E-ядра на Linux). Дубликатов среди открытых
issue и PR не нашлось (поиск: «efficiency cores», «performance cores», «profdata»).

______________________________________________________________________

## PR 1: ветка `upstream-perf-cores-warning`

**Title:** Add runtime warning when threads exceed performance cores

**Body:**

On machines with both performance and efficiency cores, a model Verilated with more threads than
there are performance cores puts at least one thread on an efficiency core. The static mtask
schedule waits on every thread, so the slowest core sets the pace for the whole model.

Measured on an Apple M4 (4 performance + 6 efficiency cores), VeeR EH1 running CoreMark, median of
7 runs, relative to `--threads 1`:

| `--threads` | Speedup |
|---|---|
| 2 | 1.58x |
| 3 | 1.78x |
| 4 | 1.70x |
| 6 | 0.92x |

With 6 threads the model is slower than single-threaded, and nothing tells the user why. The
existing oversubscription warning does not fire because 6 is below the 10 hardware threads.

This adds a runtime warning, issued when a model is added to a context, if the model's thread
count exceeds the number of performance cores. The core count comes from
`hw.perflevel0.logicalcpu` on macOS, only when `hw.nperflevels` reports more than one level;
on other systems the check is a no-op. The multithreading section of the guide gains a paragraph
on hybrid CPUs.

The test skips unless run on a Mac with performance and efficiency cores. Linux hybrid CPUs
(e.g. Intel P/E cores) could be covered later through `/sys/devices/cpu_core/cpus`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

______________________________________________________________________

## PR 2: ветка `upstream-docs-clang-pgo`

**Title:** Fix Clang compiler PGO instructions

**Body:**

The Clang recipe in the Compiler PGO section passes `-fprofile-use -fprofile-correction`.
Clang does not support `-fprofile-correction` and warns about it, and a bare `-fprofile-use`
looks for `default.profdata` in the compiler's working directory, which is the Verilator output
directory, not where the simulation wrote its profile.

The recipe now passes the absolute path of the merged profile, notes where Clang writes the
`*.profraw` files, and mentions `xcrun llvm-profdata` for macOS, where `llvm-profdata` is not on
the PATH. Checked with Apple clang 21 and `--binary`: the rebuild picks up the profile.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
