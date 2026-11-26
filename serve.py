"""Production entry point, supported on Windows and Linux."""
import os
from waitress import serve
from web import create_app

if __name__ == '__main__':
    serve(create_app(), host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), threads=4)
