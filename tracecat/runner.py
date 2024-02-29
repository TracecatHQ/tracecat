from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"message": "Hello. I am a runner."}
