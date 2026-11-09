from pydantic import BaseModel, StrictBool


class CameraRequest(BaseModel):
    camera: StrictBool = False


class ControlDecision(BaseModel):
    allow: StrictBool


class ResolutionRequest(BaseModel):
    accept: StrictBool
    close: StrictBool = False
    comment: str = ""


class CompleteStepRequest(BaseModel):
    seen_on_screen: StrictBool = False
