import argparse
import sys
import datetime
import threading
import time
import webbrowser
import logging
from pathlib import Path

try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# Fix path to allow importing from current package when run as script
try:
    from .financial_tools import get_financial_accounts, get_transactions, get_spending_analysis, connect_bank_account
except ImportError:
    sys.path.append(str(Path(__file__).parent.parent))
    from theo_ports.financial_tools import get_financial_accounts, get_transactions, get_spending_analysis, connect_bank_account

def run_server(link_token):
    if not FLASK_AVAILABLE:
        print("Error: Flask is not installed. Run `pip install flask`")
        return

    app = Flask(__name__)
    
    # Silence Flask logs
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    @app.route('/')
    def index():
        template_path = Path(__file__).parent / "templates" / "plaid_link_template.html"
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                html = f.read()
            return html.replace("{{LINK_TOKEN}}", link_token)
        except Exception as e:
            return f"Error loading template: {e}", 500

    @app.route('/exchange', methods=['POST'])
    def exchange():
        data = request.json
        public_token = data.get('public_token')
        if not public_token:
            return jsonify({'success': False, 'error': 'Missing public token'}), 400

        print(f"\n[Flask] Received public token. Exchanging...")
        msg, meta = connect_bank_account(public_token=public_token)
        
        # Schedule shutdown
        def shutdown():
            time.sleep(1)
            print("[Flask] Shutting down server...")
            try:
                # Force kill for script interaction
                import os, signal
                os.kill(os.getpid(), signal.SIGINT)
            except Exception:
                sys.exit(0)
                
        threading.Thread(target=shutdown).start()
        
        return jsonify(meta)

    print("\n[Server] Starting Plaid Link UI at http://localhost:3000")
    webbrowser.open("http://localhost:3000")
    app.run(port=3000)

def main():
    parser = argparse.ArgumentParser(description="Theo Financial Tools CLI")
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # accounts
    subparsers.add_parser('accounts', help="List connected accounts and balances")
    
    # transactions
    txn = subparsers.add_parser('transactions', help="List recent transactions")
    txn.add_argument('--days', type=int, default=30, help="Number of days to look back")
    
    # analysis
    anal = subparsers.add_parser('analysis', help="Analyze spending by category")
    anal.add_argument('--days', type=int, default=30, help="Number of days to analyze")
    
    # connect
    subparsers.add_parser('connect', help="Open Plaid Link UI to connect a bank")
    
    args = parser.parse_args()
    
    try:
        if args.command == 'accounts':
            msg, meta = get_financial_accounts()
            print(msg)
        elif args.command == 'transactions':
            end = datetime.date.today()
            start = end - datetime.timedelta(days=args.days)
            msg, meta = get_transactions(start_date=str(start), end_date=str(end))
            print(msg)
        elif args.command == 'analysis':
            end = datetime.date.today()
            start = end - datetime.timedelta(days=args.days)
            msg, meta = get_spending_analysis(start_date=str(start), end_date=str(end))
            print(msg)
        elif args.command == 'connect':
            print("Initializing Plaid Link...")
            msg, meta = connect_bank_account()
            if meta.get('success') and meta.get('link_token'):
                run_server(meta['link_token'])
            else:
                print(f"Failed to create link token: {msg}")
            
    except Exception as e:
        print(f"Error executing command: {e}")

if __name__ == "__main__":
    main()
