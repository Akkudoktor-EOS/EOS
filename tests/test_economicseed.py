"""Economic seed safety tests: original search space, fallback and price response."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from akkudoktoreos.thermal import thermaloptimization as opt

# Only the field the seed reads from the planning inputs.
REQUEST = SimpleNamespace(pattern_polish_seconds=0.0)


def setup_case(monkeypatch, prices=(10.,1.), force_first=False):
    budget=opt._PlanningBudget(30)
    p=opt._Program(budget=budget, stage='joint_candidate')
    heat=p.var(2,high=1); on=p.var(2,high=1,binary=True)
    p.eq({heat[0]:1,heat[1]:1},1)
    for h,b in zip(heat,on):
        p.row({h:1,b:-1},high=0)
        p.row({h:1,b:-.1},low=0)
    if force_first:p.lower[heat[0]]=1
    p.set_witness(range(4),[1,0,1,0])
    p.physical_validator=lambda x:{'conservation':abs(x[0]+x[1]-1)}
    def thermal(request,source,*,witness_heat):
        hints=opt._Program()
        hints.witness_values={i:float(witness_heat[j]>opt._TOL) for j,i in enumerate(on)}
        return (hints,)
    monkeypatch.setattr(opt,'_thermal_program',thermal)
    source=SimpleNamespace(max_power_w=np.ones(2),min_power_w=np.full(2,.1),fixed_heat_prefix_w=())
    return p,np.array([*prices,0.,0.]),source,heat,on,budget


def test_price_driven_pause_preserves_full_search(monkeypatch):
    p,c,s,h,ints,b=setup_case(monkeypatch)
    original=copy.deepcopy((p.lower,p.upper,p.rows,p.integer))
    opt._economic_seed(p,c,REQUEST,s,h,ints,b)
    assert b.candidate_cost_seed_used
    assert p.witness_values=={0:0.,1:1.,2:0.,3:1.}
    assert original==(p.lower,p.upper,p.rows,p.integer)
    # Reversing prices in the same original model still permits the other ON slot.
    reversed_result=p.solve(np.array([1.,10.,0.,0.]),3)
    assert reversed_result.x[0]==pytest.approx(1)
    assert reversed_result.x[1]==pytest.approx(0)


def test_comfort_requirement_overrules_expensive_pause(monkeypatch):
    p,c,s,h,ints,b=setup_case(monkeypatch,force_first=True)
    opt._economic_seed(p,c,REQUEST,s,h,ints,b)
    assert p.witness_values[0]==1
    assert p.witness_values[2]==1
    assert not b.candidate_cost_seed_used


def test_insufficient_budget_skips_optional_search(monkeypatch):
    p,c,s,h,ints,b=setup_case(monkeypatch);b.seconds=10
    def unexpected(*args,**kwargs):raise AssertionError('No time for seed')
    monkeypatch.setattr(opt._Program,'solve',unexpected)
    opt._economic_seed(p,c,REQUEST,s,h,ints,b)
    assert not b.candidate_cost_seed_used


def test_relaxation_timeout_keeps_original_witness(monkeypatch):
    p,c,s,h,ints,b=setup_case(monkeypatch)
    original=p.witness_values.copy()
    def timeout(*args,**kwargs):
        b.failed_stage='joint_cost_relaxation'
        raise opt._NoSolution('limit','Injected timeout')
    monkeypatch.setattr(opt._Program,'solve',timeout)
    opt._economic_seed(p,c,REQUEST,s,h,ints,b)
    assert p.witness_values==original
    assert b.failed_stage is None


def test_physical_rejection_never_replaces_witness(monkeypatch):
    p,c,s,h,ints,b=setup_case(monkeypatch)
    original=p.witness_values.copy()
    p.physical_validator=lambda x:{'forbidden_heat':float(x[1])}
    opt._economic_seed(p,c,REQUEST,s,h,ints,b)
    assert p.witness_values==original
    assert not b.candidate_cost_seed_used


def test_more_expensive_seed_is_not_selected(monkeypatch):
    p,c,s,h,ints,b=setup_case(monkeypatch,prices=(1.,10.))
    original=p.witness_values.copy()
    opt._economic_seed(p,c,REQUEST,s,h,ints,b)
    assert p.witness_values==original
    assert not b.candidate_cost_seed_used
