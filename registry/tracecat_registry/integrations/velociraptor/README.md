## Velociraptor gRPC integration

Last updated: 2024-11-28

Automatically generated from Velociraptor's API proto file.
Requires `grpcio-tools==1.68.0` Python package.

Downloaded from https://github.com/Velocidex/pyvelociraptor/blob/master/pyvelociraptor/api.proto

To autogenerate the Python client, run:

```bash
curl -o api.proto https://raw.githubusercontent.com/Velocidex/pyvelociraptor/master/pyvelociraptor/api.proto
cd registry/tracecat_registry/integrations/velociraptor
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. --pyi_out=. api.proto
```
