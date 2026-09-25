python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

set -a; source .env; set +a

python setup_notion_db.py       
python sync.py         