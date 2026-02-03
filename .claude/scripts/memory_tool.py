import os
import sys
import argparse
from datetime import datetime

# Path setup
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(SCRIPT_DIR)
MEMORY_FILE = os.path.join(AGENT_DIR, 'memory.md')

def save_memory(fact):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    entry = f'- [{timestamp}] {fact}\n'
    
    try:
        with open(MEMORY_FILE, 'a', encoding='utf-8') as f:
            f.write(entry)
        print(f'✅ Memory saved: {fact}')
    except Exception as e:
        print(f'❌ Error saving memory: {e}')

def main():
    parser = argparse.ArgumentParser(description='Save a fact to long-term memory.')
    parser.add_argument('fact', help='The text to remember.')
    args = parser.parse_args()
    
    save_memory(args.fact)

if __name__ == '__main__':
    main()
