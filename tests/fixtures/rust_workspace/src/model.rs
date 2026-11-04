pub struct Config {
    pub value: u32,
}

impl Config {
    pub fn new(value: u32) -> Self {
        Self { value }
    }
}

pub trait Evaluate {
    fn evaluate(&self) -> u32;
}

impl Evaluate for Config {
    fn evaluate(&self) -> u32 {
        self.value
    }
}
