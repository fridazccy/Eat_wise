import os
import numpy as np
import pandas as pd
from openai import AzureOpenAI

openai_client = AzureOpenAI(
  api_key = "AZURE_API_KEY", # use your key here
  api_version = "2023-05-15", # apparently HKUST uses a deprecated version
  azure_endpoint = "https://hkust.azure-api.net" # per HKUST instructions
)
