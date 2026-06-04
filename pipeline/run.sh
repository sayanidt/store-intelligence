#!/bin/bash
# Run the event emitter pipeline in fast mode (speed = 0)

# Resolve directories dynamically so it runs successfully from any location
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "Executing Store Intelligence Event Emitter Replay..."
python emit.py --file ../data/sample_events.jsonl --speed 0
