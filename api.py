# Install required modules: pip install fastapi uvicorn pandas openpyxl python-multipart
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import pandas as pd
import numpy as np
import re
import io
import zipfile

app = FastAPI(title="NSAP Data Processing Engine Node")

# Bypasses local cross-origin browser security restrictions entirely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def clean_string(val):
    if pd.isna(val):
        return ""
    return re.sub(r'[\s\xa0]+', ' ', str(val)).strip()

def process_file_to_df(uploaded_file: UploadFile):
    try:
        contents = uploaded_file.file.read()
        if uploaded_file.filename.endswith('.xlsx'):
            df = pd.read_excel(io.BytesIO(contents), engine='openpyxl')
        else:
            # FIX: Force the lightning-fast 'c' engine and default to standard comma delimiter.
            # We use utf-8 with fallback to latin-1 to avoid decoding freezes.
            try:
                df = pd.read_csv(io.BytesIO(contents), sep=',', engine='c', encoding='utf-8')
            except Exception:
                df = pd.read_csv(io.BytesIO(contents), sep=',', engine='c', encoding='latin-1')
        
        # Format Headers & Elements cleanly
        df.columns = [clean_string(c) for c in df.columns]
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].fillna("").astype(str).apply(clean_string)
                
        col_mapping = {
            'SubDistrict/ Municipality': 'SubDistrict', 'Sub-District / Municipality': 'SubDistrict',
            'Gram Panchayat/Ward': 'Gram_Panchayat', 'Gram Panchayat': 'Gram_Panchayat',
            'Village': 'Village', 'Applicant Name': 'Applicant_Name', 'Scheme': 'Scheme'
        }
        df = df.rename(columns=lambda x: col_mapping.get(x, x))
        
        # Map out standard seeding patterns cleanly
        if 'Aadhar No' in df.columns:
            df['Aadhaar_Status'] = df['Aadhar No'].fillna("").astype(str).apply(
                lambda x: 'No' if x.strip().lower() in ['no', '0', 'false', 'n', '', 'nan'] else 'Yes'
            )
        elif 'Aadhar Verified' in df.columns:
            df['Aadhaar_Status'] = df['Aadhar Verified'].fillna("").astype(str).apply(
                lambda x: 'Yes' if x.strip().lower() in ['yes', 'true', 'y'] else 'No'
            )
        else:
            df['Aadhaar_Status'] = 'No'
            
        return df
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File Processing Failure: {str(e)}")


def build_summary_table(df_target):
    if df_target.empty: return []
    
    # FIX: Group by both SubDistrict and Gram_Panchayat to carry the link to the frontend
    stats = df_target.groupby(['SubDistrict', 'Gram_Panchayat']).agg(
        Total_Applicants=('Applicant_Name', 'count'),
        Aadhaar_Seeded=('Aadhaar_Status', lambda x: (x == 'Yes').sum())
    ).reset_index()
    stats['Aadhaar_Seeding_Pct'] = ((stats['Aadhaar_Seeded'] / stats['Total_Applicants']) * 100).round(2)
    
    records = stats.to_dict(orient='records')
    
    # Calculate row totals safely
    total_app = int(stats['Total_Applicants'].sum())
    total_seed = int(stats['Aadhaar_Seeded'].sum())
    total_pct = round((total_seed / total_app * 100), 2) if total_app > 0 else 0
    
    records.append({
        "SubDistrict": "TOTAL",
        "Gram_Panchayat": "TOTAL",
        "Total_Applicants": total_app,
        "Aadhaar_Seeded": total_seed,
        "Aadhaar_Seeding_Pct": total_pct
    })
    return records

@app.post("/api/v1/analyze")
async def analyze_data_package(current_file: UploadFile = File(...), prior_file: UploadFile = None):
    df_curr = process_file_to_df(current_file)
    
    required = ['SubDistrict', 'Gram_Panchayat', 'Applicant_Name', 'Scheme', 'Aadhaar_Status']
    missing = [r for r in required if r not in df_curr.columns]
    if missing:
        return {"status": "error", "message": f"Missing required structural headers: {missing}"}
        
    # Isolate data metrics
    state_df = df_curr[df_curr['Scheme'].str.contains('OAPFSC', case=False, na=False)]
    central_df = df_curr[df_curr['Scheme'].str.contains('IGN', case=False, na=False)]
    
    response_data = {
        "status": "success",
        "meta": {
            "sub_districts": sorted(df_curr['SubDistrict'].dropna().unique().tolist()),
            "schemes": sorted(df_curr['Scheme'].dropna().unique().tolist()),
            "gps": sorted(df_curr['Gram_Panchayat'].dropna().unique().tolist())
        },
        "tables": {
            "state_scheme": build_summary_table(state_df),
            "central_scheme": build_summary_table(central_df)
        }
    }
    return response_data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)