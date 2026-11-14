# Регрессия по сидам на CPU и GPU Apple silicon: инструкция

Сборка из ветки `apple-gpu` содержит Verilator со всеми изменениями ветки `apple-cpu` и
инструменты для регрессии: одна модель прогоняется с тысячами сидов на CPU, на GPU или на обоих
сразу. Как это устроено и откуда цифры, описано в [README.md](README.md).

## Установка

Нужны Xcode (компилятор Metal) и `brew install autoconf bison flex help2man`. Для замера
энергии ещё `macmon`.

```bash
git checkout apple-gpu
apple/install.sh ~/opt/verilator-apple-gpu
export PATH=$HOME/opt/verilator-apple-gpu/bin:$PATH
```

| Команда | Что делает |
|---|---|
| `verilator` | Verilator; модели по умолчанию собираются с `-O2`, предупреждает о `--threads` больше числа P-ядер |
| `verilator_metal_build` | переводит собранную модель в Metal и собирает GPU-хост |
| `verilator_batch_run` | регрессия по сидам: `--on cpu`, `gpu` или `both` |
| `verilator_pgo_build` | пересобирает CPU-модель с компиляторным PGO |
| `verilator_energy` | энергия, потраченная командой (через `macmon`) |

## Требования к тестбенчу

GPU-хост сам крутит клок, сам решает, прошёл ли тест, и не печатает `$display`. Поэтому:

1. **Верхний модуль — тестбенч, клок — его вход.** Хост переключает клок перед каждым `eval`.
   Клок один.
2. **Результат — в переменной.** Объявите её с `/*verilator public_flat*/`, иначе Verilator
   может её переименовать или выбросить. При успехе запишите в неё `8'hff`, при ошибке — любое
   другое значение, затем `$finish`. Другое значение успеха задаётся опцией хоста `--pass N`.
   Экземпляр без `$finish` за `--max-half` вычислений (по умолчанию 1e9) получает `TIMEOUT`.
3. **CPU должен видеть тот же результат.** На CPU тест считается пройденным, если процесс
   завершился с кодом 0 и, при заданном `--pass-regex`, его вывод совпал с регэкспом. `$finish`
   возвращает 0 и после ошибки, поэтому печатайте строку успеха и передавайте `--pass-regex`.
4. **Код такта без файлов и строк.** На каждом такте нельзя использовать `$fwrite`, строки,
   ассоциативные массивы, классы, DPI и `--timing`. `$display` на GPU выбрасывается: лог упавшего
   сида даёт его перезапуск на CPU. Initial-блоки выполняются на CPU, поэтому `$readmemh` в
   них можно использовать.
5. **Случайные числа.** `$urandom`, `$urandom_range`, `$random`, а также `$random(seed)` и
   `$urandom(seed)` работают. Экземпляр с сидом N на GPU ведёт себя так же, как CPU-модель с
   `+verilator+seed+N`. Не поддерживаются `randomize()` и constraints, `std::process` и plusargs:
   хост передаёт модели только сид.

Шаблон:

```systemverilog
module tb(input bit clk);
    bit [7:0] status /*verilator public_flat*/;
    ...
    always @(posedge clk) begin
        if (error) begin
            $display("TEST_FAILED at cycle %0d", cyc);
            status <= 8'h1;
            $finish;
        end else if (done) begin
            $display("TEST_PASSED");
            status <= 8'hff;
            $finish;
        end
    end
endmodule
```

`main` для CPU-модели (`tb_main.cpp`):

```cpp
#include "verilated.h"

#include "Vtb.h"

int main(int argc, char** argv) {
    const std::unique_ptr<VerilatedContext> contextp{new VerilatedContext};
    contextp->commandArgs(argc, argv);
    const std::unique_ptr<Vtb> top{new Vtb{contextp.get()}};
    while (!contextp->gotFinish()) {
        top->clk = !top->clk;
        top->eval();
    }
    top->final();
    return 0;
}
```

## Сборка модели

```bash
# 1. CPU-модель, однопоточная (без --threads)
verilator --cc --exe --build -j 4 --output-split-cfuncs 1000 -CFLAGS -std=c++20 \
    tb.sv tb_main.cpp
# 2. GPU: <obj_dir> <префикс модели> <клок> <переменная результата> <выходной каталог>
verilator_metal_build obj_dir Vtb clk tb__DOT__status metal --noinline
# 3. Необязательно: PGO ускоряет прогоны на CPU на 17-26%
verilator_pgo_build --obj-dir obj_dir -- ./obj_dir/Vtb +verilator+seed+1
```

- Имя переменной результата — это имя поля в модели: `<модуль>__DOT__<имя>`. Проверить его
  можно так: `grep -o '\w*__DOT__status' obj_dir/Vtb___024root.h`.
- `--output-split-cfuncs 1000` и `--noinline` обязательны для больших дизайнов. Без них
  компилятор Metal не справляется с одной огромной функцией (на VeeR EH1 больше 12 минут).
- Хост линкуется с объектами из `obj_dir`. Если модель пересобрана, пересоберите и хост
  (шаг 2).

## Проверка на новом дизайне

Сначала прогоните несколько сидов напрямую. Хост сравнивает экземпляры с CPU-моделью: всё
состояние модели и генератора случайных чисел.

```bash
metal/host metal/sim.metallib 64 --first-seed 1 --results r.txt --verify 4
```

В выводе должно быть `state matches` для каждого проверенного экземпляра. В `r.txt` по строке
на сид: `<сид> <PASS|FAIL|TIMEOUT|ERROR> <тактов>`. Если состояние расходится, GPU-трансляция
этого дизайна неверна, и для него нужен режим `--on cpu`.

## Регрессия

```bash
verilator_batch_run --on gpu --runs 8192 --pass-regex TEST_PASSED \
    --gpu-host "metal/host metal/sim.metallib" \
    -- ./obj_dir/Vtb +verilator+seed+{seed}
```

Все опции `verilator_batch_run` пишутся до `--`. После `--` идёт команда CPU-модели, где `{seed}`
заменяется на сид.

| Режим | Когда |
|---|---|
| `--on cpu` | мало сидов или короткие тесты. GPU не нужен, `--gpu-host` можно не указывать |
| `--on gpu` | CPU нужен для другой работы. На CPU остаются только хост, проверка одного экземпляра на пакет и перезапуск упавших сидов |
| `--on both` | максимальная скорость: CPU-процессы берут сиды по одному, GPU — пакетами |

По умолчанию режим `both`, если задан `--gpu-host`, иначе `cpu`.

На VeeR EH1 (8192 сида по 250 тыс. тактов, M4):

| Режим | Скорость | Энергия на прогон |
|---|---|---|
| `cpu` (PGO, 10 процессов) | 11.6 прогона/с | 3.06 Дж |
| `gpu` | 9.4 прогона/с | 1.32 Дж |
| `both` | 18.9 прогона/с | 2.10 Дж |

Опции:

- `--runs N`, `--first-seed S` — сиды S…S+N−1.
- `-j K` — число CPU-процессов, по умолчанию все ядра.
- `--link FILE ...` — файлы, нужные initial-блокам (например, `program.hex`). Каждый прогон идёт
  в своём каталоге внутри `--work` (по умолчанию `batch`), и эти файлы туда линкуются.
- `--gpu-batch N` — сидов в пакете GPU, по умолчанию 4096 (оптимум M4). Пакет должен
  поместиться в память: хост печатает `state … GB`, и на M4 с 16 ГБ доступно около 12.7 ГБ. В
  режиме `both` первый пакет GPU берёт, только если в очереди не меньше `--gpu-batch` сидов.
- `--gpu-verify N` — сколько экземпляров каждого пакета сверять с CPU, по умолчанию 1.
- Опции хоста передаются внутри `--gpu-host`, например
  `--gpu-host "metal/host metal/sim.metallib --max-half 4000000000"`.
- `--no-rerun` — не перезапускать упавшие на GPU сиды на CPU.
- `--keep` — сохранить каталоги прошедших прогонов.

Результат:

- `FAILED seeds (…)`: упавшие сиды, их логи лежат в `<work>/seed<N>/run.log`.
- `MISMATCH`: сид упал на GPU, но прошёл на CPU. Обычно это значит, что критерий успеха на CPU
  не совпадает с переменной результата (забыт `--pass-regex`). Если критерий верный, это ошибка
  трансляции.
- Код выхода: 0 — всё прошло, 1 — есть упавшие сиды, 2 — GPU-хост упал в режиме `--on gpu`
  (лог в `<work>/gpu<k>/host.log`). В режиме `both` сиды упавшего пакета доделывает CPU.

## Замер энергии

```bash
verilator_energy --idle 60 -- verilator_batch_run --on gpu ...
```

Утилита печатает энергию всего Mac (датчик SMC) и отдельно CPU, GPU и памяти: за время
команды, в простое и сверх простоя. Чем дольше прогон, тем меньше ошибка: на минутах она
меньше процента.

## Частые проблемы

- `xcrun: error: unable to find utility "metal"` — не установлен компилятор Metal:
  `xcodebuild -downloadComponent MetalToolchain`.
- Сборка Metal идёт минутами — забыты `--output-split-cfuncs 1000` или `--noinline`.
- `vl2metal.py` или компилятор Metal падает на конструкции из пункта 4 требований — такой
  дизайн пока гоняйте с `--on cpu`.
- `TIMEOUT` — тест длиннее `--max-half` вычислений (одно вычисление — полтакта).
- `cannot allocate … GB` — уменьшите `--gpu-batch`.
