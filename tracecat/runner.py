from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

app = FastAPI(default_response_class=ORJSONResponse)


@app.get("/")
def root():
    return {"message": "Hello. I am a runner."}
