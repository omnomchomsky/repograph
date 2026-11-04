use crate::model::Config;

pub fn evaluate(config: &Config) -> u32 {
    toolkit::bump(config.value)
}
