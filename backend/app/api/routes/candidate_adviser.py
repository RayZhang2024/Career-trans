from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.api.deps import (
    CurrentUser,
    DbSession,
    get_user_candidate_adviser_profile_proposal_generation_service,
    get_user_candidate_adviser_service,
)
from app.providers.llm import (
    SemanticOutputError,
    SemanticProviderConfigurationError,
    SemanticProviderRequestError,
    SemanticProviderUnavailableError,
)
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentRead,
    CandidateAdviserClarificationAnswer,
    CandidateAdviserClarificationRead,
    CandidateAdviserIntake,
    CandidateAdviserIntakeRead,
    CandidateAdviserAreaSelectionRequest,
)
from app.schemas.candidate_adviser_journey import CandidateAdviserAreaRead, CandidateAdviserJourneyRead
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalAction,
    CandidateAdviserProfileProposalPatch,
    CandidateAdviserProfileProposalRead,
    CandidateAdviserProfileProposalGenerationRead,
    CandidateAdviserProfileProposalTransferRead,
    CandidateAdviserProfileProposalOverlapResolutionRequest,
)
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalConflict,
    CandidateAdviserProfileProposalNotFound,
    CandidateAdviserProfileProposalService,
)
from app.services.candidate_adviser_profile_proposal_generation import CandidateAdviserProfileProposalGenerationService
from app.services.candidate_adviser_journey_service import CandidateAdviserJourneyService

router = APIRouter(prefix="/candidate-adviser", tags=["candidate-adviser"])


@router.get("/refinement", response_model=CandidateAdviserJourneyRead)
def read_refinement(current_user: CurrentUser, db: DbSession) -> CandidateAdviserJourneyRead:
    return CandidateAdviserJourneyService(db).read(current_user.id)


@router.put("/refinement/areas", response_model=list[CandidateAdviserAreaRead])
def select_refinement_areas(
    payload: CandidateAdviserAreaSelectionRequest,
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_user_candidate_adviser_service),
) -> list[CandidateAdviserAreaRead]:
    try:
        return [CandidateAdviserAreaRead(
            area_key=area.area_key,
            title=area.title,
            rationale=area.rationale,
            priority_index=area.priority_index,
            selection_state=area.selection_state,
            round_number=area.round_number,
        ) for area in service.select_refinement_areas(current_user.id, payload)]
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/refinement/questions", response_model=list[CandidateAdviserClarificationRead])
def read_refinement_questions(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_user_candidate_adviser_service),
) -> list[CandidateAdviserClarificationRead]:
    try:
        return service.list_clarifications(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/refinement/questions/generate", response_model=list[CandidateAdviserClarificationRead])
def generate_refinement_questions(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_user_candidate_adviser_service),
) -> list[CandidateAdviserClarificationRead]:
    try:
        return service.generate_round_questions(current_user.id)
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except SemanticProviderUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (SemanticProviderRequestError, SemanticOutputError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/intake", response_model=CandidateAdviserIntakeRead)
def read_intake(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_user_candidate_adviser_service)) -> CandidateAdviserIntakeRead:
    intake = service.get_intake(current_user.id)
    if intake is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate adviser intake not found.")
    return intake


@router.put("/intake", response_model=CandidateAdviserIntakeRead)
def save_intake(payload: CandidateAdviserIntake, current_user: CurrentUser, service: CandidateAdviserService = Depends(get_user_candidate_adviser_service)) -> CandidateAdviserIntakeRead:
    return service.save_intake(current_user.id, payload)


@router.get("/assessment", response_model=CandidateAdviserAssessmentRead)
def read_assessment(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_user_candidate_adviser_service)) -> CandidateAdviserAssessmentRead:
    assessment = service.get_assessment(current_user.id)
    if assessment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate adviser assessment not found.")
    return assessment


@router.post("/assessment", response_model=CandidateAdviserAssessmentRead)
def generate_assessment(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_user_candidate_adviser_service), replace_review_draft: bool = Query(default=False)) -> CandidateAdviserAssessmentRead:
    try:
        return service.assess(current_user.id, regenerate=replace_review_draft)
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except SemanticProviderUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (SemanticProviderRequestError, SemanticOutputError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
@router.post("/assessment/confirm", response_model=CandidateAdviserAssessmentRead)
def confirm_assessment(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_user_candidate_adviser_service)) -> CandidateAdviserAssessmentRead:
    try:
        return service.confirm_assessment(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/clarifications", response_model=list[CandidateAdviserClarificationRead])
def list_clarifications(current_user: CurrentUser, service: CandidateAdviserService = Depends(get_user_candidate_adviser_service)) -> list[CandidateAdviserClarificationRead]:
    try:
        return service.list_clarifications(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/clarifications/{clarification_id}/answer", response_model=CandidateAdviserClarificationRead)
def answer_clarification(
    clarification_id: str,
    payload: CandidateAdviserClarificationAnswer,
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_user_candidate_adviser_service),
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
    service: CandidateAdviserService = Depends(get_user_candidate_adviser_service),
) -> CandidateAdviserClarificationRead:
    try:
        return service.confirm_clarification(current_user.id, clarification_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clarification not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/clarifications/{clarification_id}/profile-proposals",
    response_model=CandidateAdviserProfileProposalGenerationRead,
)
def generate_profile_proposals(
    clarification_id: str,
    current_user: CurrentUser,
    service: CandidateAdviserProfileProposalGenerationService = Depends(
        get_user_candidate_adviser_profile_proposal_generation_service
    ),
) -> CandidateAdviserProfileProposalGenerationRead:
    try:
        return service.generate(current_user.id, clarification_id)
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clarification not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Candidate Adviser proposal generation is not configured.",
        ) from exc
    except SemanticProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Candidate Adviser proposal generation is temporarily unavailable.",
        ) from exc
    except (SemanticProviderRequestError, SemanticOutputError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Candidate Adviser proposal generation failed to return valid proposals.",
        ) from exc


@router.post("/clarifications/{clarification_id}/profile-enrichment/defer", status_code=status.HTTP_204_NO_CONTENT)
def defer_profile_enrichment(
    clarification_id: str,
    current_user: CurrentUser,
    service: CandidateAdviserProfileProposalGenerationService = Depends(
        get_user_candidate_adviser_profile_proposal_generation_service
    ),
) -> None:
    try:
        service.defer(current_user.id, clarification_id)
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clarification not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/profile-proposals", response_model=list[CandidateAdviserProfileProposalRead])
def list_profile_proposals(
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[CandidateAdviserProfileProposalRead]:
    return CandidateAdviserProfileProposalService(db).list_for_user(current_user.id, limit=limit)


@router.get("/profile-proposals/{proposal_id}", response_model=CandidateAdviserProfileProposalRead)
def read_profile_proposal(
    proposal_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> CandidateAdviserProfileProposalRead:
    service = CandidateAdviserProfileProposalService(db)
    try:
        return service.get_for_user(current_user.id, proposal_id)
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile proposal not found.") from exc


@router.patch("/profile-proposals/{proposal_id}", response_model=CandidateAdviserProfileProposalRead)
def edit_profile_proposal(
    proposal_id: str,
    payload: CandidateAdviserProfileProposalPatch,
    current_user: CurrentUser,
    db: DbSession,
) -> CandidateAdviserProfileProposalRead:
    service = CandidateAdviserProfileProposalService(db)
    try:
        return service.edit_pending(
            current_user.id,
            proposal_id,
            expected_revision=payload.expected_revision,
            proposed_update=payload.proposed_update,
        )
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile proposal not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/profile-proposals/{proposal_id}/resolve-overlap",
    response_model=CandidateAdviserProfileProposalRead,
)
def resolve_profile_proposal_overlap(
    proposal_id: str,
    payload: CandidateAdviserProfileProposalOverlapResolutionRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> CandidateAdviserProfileProposalRead:
    try:
        return CandidateAdviserProfileProposalService(db).resolve_overlap(
            current_user.id, proposal_id, payload
        )
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile proposal not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/profile-proposals/{proposal_id}/reject", response_model=CandidateAdviserProfileProposalRead)
def reject_profile_proposal(
    proposal_id: str,
    payload: CandidateAdviserProfileProposalAction,
    current_user: CurrentUser,
    db: DbSession,
) -> CandidateAdviserProfileProposalRead:
    service = CandidateAdviserProfileProposalService(db)
    try:
        return service.reject_pending(
            current_user.id,
            proposal_id,
            expected_revision=payload.expected_revision,
        )
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile proposal not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/profile-proposals/{proposal_id}/transfer",
    response_model=CandidateAdviserProfileProposalTransferRead,
)
def transfer_profile_proposal(
    proposal_id: str,
    payload: CandidateAdviserProfileProposalAction,
    current_user: CurrentUser,
    db: DbSession,
) -> CandidateAdviserProfileProposalTransferRead:
    try:
        return CandidateAdviserProfileProposalService(db).transfer_to_profile_revision(
            current_user.id,
            proposal_id,
            expected_revision=payload.expected_revision,
        )
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile proposal not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/profile-proposals/{proposal_id}/apply", response_model=CandidateAdviserProfileProposalRead)
def apply_profile_proposal(
    proposal_id: str,
    payload: CandidateAdviserProfileProposalAction,
    current_user: CurrentUser,
    db: DbSession,
) -> CandidateAdviserProfileProposalRead:
    try:
        return CandidateAdviserProfileProposalService(db).apply_to_profile(
            current_user.id,
            proposal_id,
            expected_revision=payload.expected_revision,
        )
    except CandidateAdviserProfileProposalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile proposal not found.") from exc
    except CandidateAdviserProfileProposalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
