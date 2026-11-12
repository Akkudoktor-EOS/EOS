//! CPython's `random` module (Mersenne Twister MT19937), reproduced bit-identically.
//!
//! Covers exactly what GENETIC and DEAP call: `seed(int)`, `random()`,
//! `getrandbits`, `randbelow` (via `randrange`/`randint`/`choice`), `sample` and
//! `uniform`. Based on CPython 3.13 `Modules/_randommodule.c` and `Lib/random.py`;
//! checked against CPython in the probe build `eos-bench/native/py-random-probe` and in
//! `tests/pyrandom.rs`.

const N: usize = 624;
const M: usize = 397;

#[derive(Clone)]
pub struct PyRandom {
    mt: [u32; N],
    idx: usize,
}

impl PyRandom {
    fn init_genrand(s: u32) -> PyRandom {
        let mut mt = [0u32; N];
        mt[0] = s;
        for i in 1..N {
            mt[i] = 1_812_433_253u32.wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_add(i as u32);
        }
        PyRandom { mt, idx: N }
    }

    /// `random.seed(n)` for an integer (CPython takes the absolute value).
    pub fn seed(n: u64) -> PyRandom {
        let mut key: Vec<u32> = Vec::new();
        let mut x = n;
        if x == 0 {
            key.push(0);
        }
        while x > 0 {
            key.push(x as u32);
            x >>= 32;
        }
        let mut m = PyRandom::init_genrand(19_650_218);
        let (mut i, mut j) = (1usize, 0usize);
        let kl = key.len();
        for _ in 0..N.max(kl) {
            m.mt[i] = (m.mt[i] ^ (m.mt[i - 1] ^ (m.mt[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                m.mt[0] = m.mt[N - 1];
                i = 1;
            }
            if j >= kl {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            m.mt[i] =
                (m.mt[i] ^ (m.mt[i - 1] ^ (m.mt[i - 1] >> 30)).wrapping_mul(1_566_083_941)).wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                m.mt[0] = m.mt[N - 1];
                i = 1;
            }
        }
        m.mt[0] = 0x8000_0000;
        m
    }

    /// Seed from the system time and the process ID (no fixed seed; Python uses os.urandom –
    /// the result is not reproducible in that case anyway).
    pub fn from_entropy() -> PyRandom {
        let t = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0);
        let pid = u64::from(std::process::id());
        PyRandom::seed(t ^ (pid << 48) ^ (pid.rotate_left(17)))
    }

    #[inline]
    fn genrand_u32(&mut self) -> u32 {
        if self.idx >= N {
            for k in 0..N {
                let y = (self.mt[k] & 0x8000_0000) | (self.mt[(k + 1) % N] & 0x7fff_ffff);
                let mut v = self.mt[(k + M) % N] ^ (y >> 1);
                if y & 1 != 0 {
                    v ^= 0x9908_b0df;
                }
                self.mt[k] = v;
            }
            self.idx = 0;
        }
        let mut y = self.mt[self.idx];
        self.idx += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// `random.random()`: 53-bit float in [0, 1).
    #[inline]
    pub fn random(&mut self) -> f64 {
        let a = f64::from(self.genrand_u32() >> 5);
        let b = f64::from(self.genrand_u32() >> 6);
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }

    /// `random.getrandbits(k)` for k ≤ 64.
    #[inline]
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        if k == 0 {
            return 0;
        }
        if k <= 32 {
            return u64::from(self.genrand_u32() >> (32 - k));
        }
        let lo = u64::from(self.genrand_u32());
        let hi = u64::from(self.genrand_u32() >> (64 - k));
        lo | (hi << 32)
    }

    /// `_randbelow_with_getrandbits(n)`, n > 0.
    #[inline]
    pub fn randbelow(&mut self, n: u64) -> u64 {
        // Python raises ValueError("empty range"); a panic becomes NativeRunError in the binding.
        assert!(n > 0, "empty range for randrange");
        let k = 64 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// `random.randrange(n)`.
    #[inline]
    pub fn randrange(&mut self, n: usize) -> usize {
        self.randbelow(n as u64) as usize
    }

    /// `random.randint(a, b)` (a ≤ b, both inclusive).
    #[inline]
    pub fn randint(&mut self, a: i64, b: i64) -> i64 {
        debug_assert!(b >= a);
        a + self.randbelow((b - a + 1) as u64) as i64
    }

    /// Index for `random.choice(seq)` with `len(seq) == n`.
    #[inline]
    pub fn choice_index(&mut self, n: usize) -> usize {
        self.randbelow(n as u64) as usize
    }

    /// `random.uniform(a, b)`.
    #[inline]
    pub fn uniform(&mut self, a: f64, b: f64) -> f64 {
        a + (b - a) * self.random()
    }

    /// Indices of `random.sample(population, k)` for a sequence of length n.
    pub fn sample_indices(&mut self, n: usize, k: usize) -> Vec<usize> {
        let mut result = vec![0usize; k];
        let mut setsize: usize = 21;
        if k > 5 {
            let e = ((k as f64 * 3.0).ln() / 4f64.ln()).ceil() as u32;
            setsize += 4usize.pow(e);
        }
        if n <= setsize {
            let mut pool: Vec<usize> = (0..n).collect();
            for (i, slot) in result.iter_mut().enumerate() {
                let j = self.randbelow((n - i) as u64) as usize;
                *slot = pool[j];
                pool[j] = pool[n - i - 1];
            }
        } else {
            let mut selected = std::collections::HashSet::with_capacity(k);
            for slot in result.iter_mut() {
                let mut j = self.randbelow(n as u64) as usize;
                while selected.contains(&j) {
                    j = self.randbelow(n as u64) as usize;
                }
                selected.insert(j);
                *slot = j;
            }
        }
        result
    }
}
