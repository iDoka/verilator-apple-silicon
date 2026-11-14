// Seeded random regression test for the vl2metal RNG port: random traffic through a FIFO,
// checked by a scoreboard. Each seed takes a different number of cycles; a planted bug fires
// on rare random values, so some seeds fail.
module rng_tb (input clk);
    localparam int N = 2000;  // transactions per test
    bit [7:0] gpu_status /*verilator public_flat*/;
    int unsigned cyc = 0, pushed = 0, popped = 0;
    bit [31:0] fifo [0:15];
    bit [3:0] wp = 0, rp = 0;
    bit [4:0] count = 0;
    bit [31:0] expect_q [0:15];
    bit [63:0] mix = 0;
    bit [95:0] wide = 0;
    int seed_var = 7;
`ifdef RESEED
    initial mix = {$urandom, $urandom};  // initial state depends on the seed
`endif

    always @(posedge clk) begin
        automatic bit do_push = count < 16 && pushed < N && $urandom_range(3, 0) != 0;
        automatic bit do_pop = count > 0 && $urandom_range(0, 2) == 0;
        automatic bit [31:0] data = $urandom;
        cyc <= cyc + 1;
        mix <= mix ^ {$urandom, $random} ^ 64'($urandom_range(1000, 10));
        wide <= wide ^ {$urandom, $urandom, $urandom};
`ifdef RESEED
        // Reseeding makes every seed continue alike; only for testing the port
        if (cyc == 100) seed_var <= $random(seed_var);
        if (cyc == 200) seed_var <= $urandom(cyc);
`endif
        if (do_push) begin
            fifo[wp] <= data;
            expect_q[wp] <= data;
            wp <= wp + 1;
            pushed <= pushed + 1;
        end
        if (do_pop) begin
            // Planted bug: one value in 2^13 is corrupted on the way out
            if (fifo[rp] != expect_q[rp] || fifo[rp][12:0] == 13'h15a5) begin
                $display("TEST_FAILED at cycle %0d", cyc);
                gpu_status <= 8'h1;
                $finish;
            end
            rp <= rp + 1;
            popped <= popped + 1;
        end
        count <= count + 5'(do_push) - 5'(do_pop);
        if (popped == N) begin
            $display("TEST_PASSED cycles %0d mix %x wide %x seed %0d", cyc, mix, wide, seed_var);
            gpu_status <= 8'hff;
            $finish;
        end
    end
endmodule
