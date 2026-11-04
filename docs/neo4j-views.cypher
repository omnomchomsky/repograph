// Run each query separately in Neo4j Browser. Set these Browser parameters first:
// These values match the bundled Rust fixture example in README.md.
// :param repo_id => 'example-workspace'
// :param symbol => 'rust-analyzer cargo graph-fixture 0.1.0 lib/model/Config#'
// List repositories to obtain a different repo_id:
MATCH (repo:RepoGraphNode:Repository)
RETURN repo.name AS name, repo.repo_id AS repo_id, repo.path AS path;

// 1. Package overview: workspace members, direct dependencies and crate targets.
MATCH p=(pkg:RepoGraphNode:Package {repo_id: $repo_id})-[:DEPENDS_ON|HAS_TARGET]->()
WHERE pkg.workspace_member
RETURN p LIMIT 300;

// 2. Module/type structure: hide locals and parameters to reduce clutter.
MATCH p=(parent:RepoGraphNode {repo_id: $repo_id})-[:CONTAINS]->(child:Symbol)
WHERE child.external = false
  AND (child:Module OR child:Struct OR child:Enum OR child:Trait OR child:Function
       OR child:Method OR child:Impl)
RETURN p LIMIT 500;

// 3. Selected symbol: immediate neighbors, including definition and reference files,
// plus the complete package/crate/module ownership path. Click edges for coordinates.
MATCH (s:RepoGraphNode:Symbol {repo_id: $repo_id, scip_symbol: $symbol})
MATCH p=(s)-[:DEFINED_IN|REFERENCES|CONTAINS]-()
RETURN p LIMIT 150
UNION
MATCH (s:RepoGraphNode:Symbol {repo_id: $repo_id, scip_symbol: $symbol})
MATCH p=(:Package)-[:HAS_TARGET]->(:Crate)-[:CONTAINS*0..64]->(s)
RETURN p LIMIT 50;
