#[test]
fn smoke() {
    let config = graph_fixture::model::Config::new(2);
    assert_eq!(graph_fixture::engine::evaluate(&config), 3);
}
