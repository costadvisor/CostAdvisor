import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.scenario import CostScenario
from app.routers.auth import get_current_user
from app.schemas.scenario import ScenarioCreate, ScenarioOut
from app.services.audit import log_event
from app.services.permissions import require_permission

router = APIRouter()


@router.get("/", response_model=list[ScenarioOut])
def list_scenarios(
    team_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return system scenarios + team-specific scenarios (only if caller is a member)."""
    if team_id is not None:
        require_permission(db, current_user, team_id, "scenarios.view")
        query = db.query(CostScenario).filter(
            (CostScenario.is_system == True) |  # noqa: E712
            (CostScenario.team_id == team_id)
        )
    else:
        query = db.query(CostScenario).filter(CostScenario.is_system == True)  # noqa: E712
    return query.all()


@router.post("/", response_model=ScenarioOut, status_code=201)
def create_scenario(
    team_id: uuid.UUID,
    data: ScenarioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "scenarios.edit")
    scenario = CostScenario(
        name=data.name,
        description=data.description,
        is_system=False,
        team_id=team_id,
        breakdown=data.breakdown,
    )
    db.add(scenario)
    db.flush()
    log_event(db, team_id, current_user.id, "create", "scenario", str(scenario.id),
              new_value={"name": data.name})
    db.expunge(scenario)
    db.commit()
    return scenario


@router.delete("/{scenario_id}")
def delete_scenario(
    scenario_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scenario = db.query(CostScenario).filter(CostScenario.id == scenario_id).first()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if scenario.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system scenarios")
    if scenario.team_id is not None:
        require_permission(db, current_user, scenario.team_id, "scenarios.delete")
        log_event(db, scenario.team_id, current_user.id, "delete", "scenario", str(scenario.id),
                  previous_value={"name": scenario.name})
    db.delete(scenario)
    db.commit()
    return {"status": "deleted"}
