"""Start the configured local database, then the API (also suitable as an IDE run target)."""
import os
import uvicorn
from setup_local import ensure_local

if __name__ == '__main__':
    ensure_local()
    uvicorn.run('main:app',host='127.0.0.1',port=int(os.getenv('PORT','8000')))
