#!/bin/bash

# Chạy cả 2 script song song
bash app/docker_run.sh & bash app/docker_run_add_new.sh & bash app/docker_run_update_agent & wait