SCIP Protobuf bindings
======================

`scip.proto` is vendored from [scip-code/scip](https://github.com/scip-code/scip/blob/e01e97efac2f6b8c266b4d04825f1f1eab7b8f6c/scip.proto),
revision `e01e97efac2f6b8c266b4d04825f1f1eab7b8f6c`, retrieved 2026-11-02.
The upstream Apache 2.0 license is included as `LICENSE`.

Schema SHA-256: `b38021b65ef90cbbf6af9c829ff75192859ad9b5da05439ef154bea4ceb2bf03`.

Generate `scip_pb2.py` with:

```sh
uv run --with grpcio-tools==1.78.0 python -m grpc_tools.protoc \
  -I repograph/proto --python_out=repograph/proto repograph/proto/scip.proto
```

The generated code requires protobuf >=6.31.1. No compiler or SCIP CLI is needed
at runtime. Both historical packed ranges and current typed ranges are supported.
