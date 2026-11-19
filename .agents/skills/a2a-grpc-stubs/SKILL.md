---
name: a2a-grpc-stubs
description: Use when the A2A gRPC proto changes, or when you regenerate or touch the generated modules penguiflow_a2a/grpc/a2a_pb2.py, a2a_pb2_grpc.py or their .pyi stubs. Gives the protoc command, the source proto (docs/spec/a2a.proto) and the relative-import fix.
---

# A2A gRPC generated code

`penguiflow_a2a/grpc/` holds generated code: `a2a_pb2.py` and `a2a_pb2_grpc.py` from protoc, and the `a2a_pb2.pyi` and `a2a_pb2_grpc.pyi` stubs from mypy-protobuf. The source is `docs/spec/a2a.proto`. `docs/specification/grpc/a2a.proto` is a different revision and does not match the committed descriptor.

**Why:** ruff and mypy skip the generated `.py` files, and mypy types them through the `.pyi` stubs (see quality-gates). Stubs that drift from the `.py` files hide real type errors.

## Regenerate

Run from the repo root. `mypy-protobuf` and `types-grpcio` are in the `dev` group; protoc comes with `grpcio-tools`.

```sh
SITE=$(uv run --frozen python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
uv run --frozen python -m grpc_tools.protoc \
  -I docs/spec -I "$SITE" -I "$SITE/grpc_tools/_proto" \
  --python_out=penguiflow_a2a/grpc --grpc_python_out=penguiflow_a2a/grpc \
  --mypy_out=penguiflow_a2a/grpc --mypy_grpc_out=penguiflow_a2a/grpc \
  docs/spec/a2a.proto
sed -i '' -e 's/^import a2a_pb2 as a2a__pb2$/from . import a2a_pb2 as a2a__pb2/' \
  -e 's/^import a2a_pb2 as _a2a_pb2$/from . import a2a_pb2 as _a2a_pb2/' \
  penguiflow_a2a/grpc/a2a_pb2_grpc.py penguiflow_a2a/grpc/a2a_pb2_grpc.pyi
```

(`sed -i ''` is the macOS form. On GNU sed, drop the `''`.)

## Rules

1. Never edit the generated files by hand, apart from the relative-import fix above. protoc writes `import a2a_pb2`, which fails inside the package.
2. Use mypy-protobuf's `--mypy_out`, not protoc's `--pyi_out`. protoc's stubs fail strict mypy (missing generic type parameters).
3. The `# type: ignore` comments in `a2a_pb2_grpc.pyi` come from the generator. They are part of the generated code, and with `types-grpcio` installed they are used.
4. After regenerating, run the quality gates.

**How to apply:** change the proto, then run the commands above, then the gates.
