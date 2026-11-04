pub mod model;

#[path = "odd_location.rs"]
pub mod engine;

pub mod inline {
    pub struct Config;

    pub fn run() -> u32 {
        let config = crate::model::Config::new(7);
        let callback = crate::engine::evaluate;
        callback(&config)
    }
}
