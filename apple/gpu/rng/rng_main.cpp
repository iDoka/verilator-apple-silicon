// CPU testbench for rng_tb: toggles the clock until $finish
#include "verilated.h"

#include "Vrng_tb.h"

int main(int argc, char** argv) {
    const std::unique_ptr<VerilatedContext> contextp{new VerilatedContext};
    contextp->commandArgs(argc, argv);
    const std::unique_ptr<Vrng_tb> top{new Vrng_tb{contextp.get()}};
    while (!contextp->gotFinish()) {
        top->clk = !top->clk;
        top->eval();
    }
    top->final();
    return 0;
}
