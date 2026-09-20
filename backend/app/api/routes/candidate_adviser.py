from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, get_candidate_adviser_service
from app.providers.llm import SemanticOutputError, SemanticProviderConfigurationError, SemanticProviderRequestError, SemanticProviderUnavailableError
from app.schemas.candidate_adviser import CandidateAdviserAssessmentRead, CandidateAdviserClarificationAnswer, CandidateAdviserClarificationRead, CandidateAdviserIntake, CandidateAdviserIntakeRead
from app.services.candidate_adviser_service import CandidateAdviserService

router = APIRouter(prefix="/candidate-adviser", tags=["candidate-adviser"])


@router.get("/intake", response_model=CandidateAdviserIntakeRead)
def read_intake(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_candidate_adviser_service)) -> CandidateAdviserIntakeRead:
    intake = service.get_intake(current_user.id)
    if intake is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate adviser intake not found.")
    return intake


@router.put("/intake", response_model=CandidateAdviserIntakeRead)
def save_intake(payload: CandidateAdviserIntake, current_user: CurrentUser, service: CandidateAdviserService = Depends(get_candidate_adviser_service)) -> CandidateAdviserIntakeRead:
    return service.save_intake(current_user.id, payload)


@router.get("/assessment", response_model=CandidateAdviserAssessmentRead)
def read_assessment(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_candidate_adviser_service)) -> CandidateAdviserAssessmentRead:
    assessment = service.get_assessment(current_user.id)
    if assessment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate adviser assessment not found.")
    return assessment


@router.post("/assessment", response_model=CandidateAdviserAssessmentRead)
def generate_assessment(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_candidate_adviser_service)) -> CandidateAdviserAssessmentRead:
    try:
        return service.assess(current_user.id)
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except SemanticProviderUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (SemanticProviderRequestError, SemanticOutputError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/assessment/confirm", response_model=CandidateAdviserAssessmentRead)
def confirm_assessment(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_candidate_adviser_service)) -> CandidateAdviserAssessmentRead:
    try:
        return service.confirm_assessment(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/clarifications", response_model=list[CandidateAdviserClarificationRead])
def list_clarifications(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_candidate_adviser_service)) -> list[CandidateAdviserClarificationRead]:
    try:
        return service.list_clarifications(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/clarifications/{clarification_id}/answer", response_model=CandidateAdviserClarificationRead)
def answer_clarification(
    clarification_id: str,
    payload: CandidateAdviserClarificationAnswer,
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateAdviserClarificationRead:
    try:
        return service.answer_clarification(current_user.id, clarification_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clarification not found.") from exc
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except SemanticProviderUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (SemanticProviderRequestError, SemanticOutputError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/clarifications/{clarification_id}/confirm", response_model=CandidateAdviserClarificationRead)
def confirm_clarification(
    clarification_id: str,
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateAdviserClarificationRead:
    try:
        return service.confirm_clarification(current_user.id, clarification_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clarification not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
