fn main() {
    let config = graph_fixture::model::Config::new(3);
    let _value = graph_fixture::engine::evaluate(&config);
}
