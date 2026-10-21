# Vendored `bfrt_grpc`

This directory contains a copy of the BF Runtime gRPC Python client from
[open-p4studio](https://github.com/p4lang/open-p4studio), Copyright (C) 2024
Intel Corporation, licensed under the Apache License 2.0.

| File | Upstream source |
|------|-----------------|
| `client.py`, `info_parse.py` | `pkgsrc/bf-drivers/src/bf_rt/bfruntime_grpc_client/python/` |
| `bfruntime.proto` | `pkgsrc/bf-drivers/src/bf_rt/proto/bfruntime.proto` |
| `bfruntime_pb2.py`, `bfruntime_pb2_grpc.py` | generated from `bfruntime.proto` (protoc, Protobuf Python 4.25) |

## Local changes

- `client.py`: imports of the generated stubs made relative
  (`from . import bfruntime_pb2`) so the package can be vendored.
- `info_parse.py`: `data_field_allowed_choices_get()` returns the allowed values
  of string/enum fields (e.g. `$SPEED`) instead of the field type.
- `info_parse.py`: actions keep their `action_scope` from bf-rt.json, exposed
  through the new `_TableInfo.action_scope_get()`.
- `client.py`, `info_parse.py`: loggers renamed to `__name__` and no longer
  add handlers or force a log level at import time.

## Regenerating the stubs

```bash
pip install grpcio-tools googleapis-common-protos
python -m grpc_tools.protoc -I. -I<path-to>/googleapis \
    --python_out=. --grpc_python_out=. bfruntime.proto
sed -i 's/^import bfruntime_pb2/from . import bfruntime_pb2/' bfruntime_pb2_grpc.py
```
