from pydantic import BaseModel, StrictBool


class CameraRequest(BaseModel):
    camera: StrictBool = False


class ControlDecision(BaseModel):
    allow: StrictBool


class ResolutionRequest(BaseModel):
    accept: StrictBool


class CompleteStepRequest(BaseModel):
    seen_on_screen: StrictBool = False
