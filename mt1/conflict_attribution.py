"""Evidence-only conflict attribution. Never changes Union quarantine policy."""
from decimal import Decimal, ROUND_HALF_UP


def classify_values(values):
    nums=sorted({Decimal(str(v)) for v in values})
    if not nums or any(not n.is_finite() or n<=0 for n in nums):
        return {'kind':'invalid_or_nonpositive','admissible':False}
    if len(nums)==1:return {'kind':'equal','admissible':False}
    # A falsifiable observation, NOT a license to round all historical inputs.
    coarse={n.quantize(Decimal('.001'),rounding=ROUND_HALF_UP) for n in nums}
    rounded= len(coarse)==1 and any(n==next(iter(coarse)) for n in nums)
    return {'kind':'compatible_with_3dp_rounding' if rounded else 'substantive_difference',
        'values':[str(n) for n in nums],'spread':str(nums[-1]-nums[0]),
        'relative_spread':str((nums[-1]-nums[0])/nums[0]),'admissible':False,
        'note':'rounding compatibility is not proof of provider precision or historical version; no winner'}


def ratio_diagnostic(pairs):
    ratios=[Decimal(str(b))/Decimal(str(a)) for a,b in pairs if Decimal(str(a))>0]
    return {'overlap_days':len(ratios),'distinct_old_levels':len({str(a) for a,b in pairs}),
        'scale_identifiable_from_variation':len({str(a) for a,b in pairs})>1,'exact_constant_scale':bool(ratios) and len(set(ratios))==1,
        'ratio_min':str(min(ratios)) if ratios else None,'ratio_max':str(max(ratios)) if ratios else None,
        'normalization_authorized':False}
