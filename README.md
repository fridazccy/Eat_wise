# open streamlit - 
# cd /c/Users/frida/Eat_wise/server 
# python -m streamlit run streamlit_app.py --server.headless true

# eatwise
## Local environment setup (important)

1. Copy `.env.example` to a local file and fill in real values on your machine (do not commit this file):
   - Recommended filename (local only): `server/.env` or `server/api.env`

   Example (on your machine):
   ```
   AZURE_API_KEY=your_real_azure_api_key_here
   AZURE_API_VERSION=2023-05-15
   AZURE_ENDPOINT=https://hkust.azure-api.net
   AZURE_OPENAI_DEPLOYMENT=your-deployment-name
   PORT=4000
   ```

2. `api.env` is ignored by git (see `.gitignore`) — do not add secrets to the repository.

3. To run locally:
   - Node backend: 
     ```
     cd server
     npm install
     npm run dev
     ```
   - Streamlit (Python prototype):
     ```
     cd server
     pip install streamlit python-dotenv requests
     streamlit run streamlit_app.py
     ```

4. If a secret was published accidentally, rotate it immediately in Azure and remove any tracked `.env` file from the repo history.
