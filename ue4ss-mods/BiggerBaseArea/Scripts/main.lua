-- Enlarges every base's radius (build/work area) by a modest factor.
-- Server-side only; clients need nothing installed. Source of truth lives in the
-- pal-web-gui repo (ue4ss-mods/); copy into Pal/Binaries/Win64/ue4ss/Mods/.

-- Kept modest: the client still draws the boundary circle at the vanilla radius,
-- so a large multiplier makes the buildable area confusingly disagree with it.
local MULTIPLIER = 1.25

local seen = {}

local function enlarge(model)
    if not model:IsValid() then return end
    local key = model:GetAddress()
    if seen[key] then return end
    seen[key] = true
    local ok, err = pcall(function()
        local current = model.AreaRange
        model.AreaRange = current * MULTIPLIER
        print(string.format("[BiggerBaseArea] AreaRange %.0f -> %.0f\n", current, current * MULTIPLIER))
    end)
    if not ok then
        print("[BiggerBaseArea] could not adjust a base: " .. tostring(err) .. "\n")
    end
end

-- Bases created or loaded from here on
NotifyOnNewObject("/Script/Pal.PalBaseCampModel", enlarge)

-- Bases that already existed when this mod started
ExecuteWithDelay(15000, function()
    local models = FindAllOf("PalBaseCampModel")
    if not models then
        print("[BiggerBaseArea] no bases found yet\n")
        return
    end
    for _, model in ipairs(models) do enlarge(model) end
end)
