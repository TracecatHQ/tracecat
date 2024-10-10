#!/bin/bash

pip install cryptography >/dev/null 2>&1;
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /init-dir/db-encryption-key
