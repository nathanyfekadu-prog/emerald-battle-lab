"use strict";
var __read = (this && this.__read) || function (o, n) {
    var m = typeof Symbol === "function" && o[Symbol.iterator];
    if (!m) return o;
    var i = m.call(o), r, ar = [], e;
    try {
        while ((n === void 0 || n-- > 0) && !(r = i.next()).done) ar.push(r.value);
    }
    catch (error) { e = { error: error }; }
    finally {
        try {
            if (r && !r.done && (m = i["return"])) m.call(i);
        }
        finally { if (e) throw e.error; }
    }
    return ar;
};
var __spreadArray = (this && this.__spreadArray) || function (to, from, pack) {
    if (pack || arguments.length === 2) for (var i = 0, l = from.length, ar; i < l; i++) {
        if (ar || !(i in from)) {
            if (!ar) ar = Array.prototype.slice.call(from, 0, i);
            ar[i] = from[i];
        }
    }
    return to.concat(ar || Array.prototype.slice.call(from));
};
var __values = (this && this.__values) || function(o) {
    var s = typeof Symbol === "function" && Symbol.iterator, m = s && o[s], i = 0;
    if (m) return m.call(o);
    if (o && typeof o.length === "number") return {
        next: function () {
            if (o && i >= o.length) o = void 0;
            return { value: o && o[i++], done: !o };
        }
    };
    throw new TypeError(s ? "Object is not iterable." : "Symbol.iterator is not defined.");
};
exports.__esModule = true;

var _1 = require(".");
var field_1 = require("./field");
var gen789_1 = require("./mechanics/gen789");
var util_1 = require("./mechanics/util");
var trappingMoveNames = ['Whirlpool', 'Fire Spin', 'Sand Tomb', 'Magma Storm', 'Infestation', 'Wrap', 'Bind'];
var highCritRatioMoveNames = [
    "Aeroblast", "Air Cutter", "Attack Order",
    "Blaze Kick", "Crabhammer", "Cross Chop", "Cross Poison", "Drill Run",
    "Karate Chop", "Leaf Blade", "Night Slash", "Poison Tail", "Psycho Cut",
    "Razor Leaf", "Razor Wind", "Shadow Claw", "Sky Attack", "Slash",
    "Spacial Rend", "Stone Edge"
];
var twoHitMoves = ["Bonemerang", "Double Hit", "Double Iron Bash", "Double Kick", "Dragon Darts",
    "Dual Chop", "Dual Wingbeat", "Gear Grind", "Tachyon Cutter", "Twin Beam", "Twineedle"
];
var threeHitMoves = [
    "Arm Thrust", "Barrage", "Bone Rush", "Bullet Seed", "Comet Punch", "Double Slap",
    "Fury Attack", "Icicle Spear", "Pin Missile", "Rock Blast", "Scale Shot", "Spike Cannon",
    "Surging Strikes", "Tail Slap", "Triple Dive", "Water Shuriken"
];
var zeroBPButNotStatus = ["(No Move)", "Electro Ball", "Metal Burst", "Endeavor", "Bide",
    "Seismic Toss", "Punishment", "Flail", "Reversal", "Gyro Ball", "Magnitude", "Heat Crash",
    "Heavy Slam", "Present", "Natural Gift", "Beat Up", "Fissure", "Guillotine", "Horn Drill", "Super Fang",
    "Low Kick", "Sheer Cold", "Final Gambit", "Mirror Coat", "Nature's Madness", "Psywave", "Night Shade", "Dragon Rage",
    "Sonic Boom", "Spit Up", "Trump Card", "Grass Knot", "Wring Out", "Nature Power", "Pain Split",
    "Return"
];
var soundMoves = ["Boomburst", "Bug Buzz", "Chatter",
    "Clanging Scales", "Clangorous Soul", "Clangorous Soulblaze",
    "Confide", "Disarming Voice", "Echoed Voice", "Eerie Spell",
    "Grass Whistle", "Growl", "Heal Bell", "Howl", "Hyper Voice",
    "Metal Sound", "Noble Roar", "Overdrive", "Parting Shot",
    "Perish Song", "Psychic Noise", "Relic Song", "Roar",
    "Round", "Screech", "Sing", "Snarl", "Snore", "Sparkling Aria",
    "Supersonic", "Uproar"];
var offensiveSetup = [
    "Dragon Dance", "Shift Gear", "Swords Dance", "Howl",
    "Sharpen", "Meditate", "Hone Claws", "Charge Beam", "Power-Up Punch",
    "Swords Dance", "Howl", "Dragon Dance", "Hone Claws",
    "Growth"
];
var defensiveSetup = [
    "Acid Armor", "Barrier", "Cotton Guard", "Harden", "Iron Defense",
    "Stockpile", "Cosmic Power"
];
var powderMoves = [
    "Cotton Spore", "Magic Powder", "Poison Powder", "Powder", "Rage Powder", "Sleep Powder", "Spore", "Stun Spore"
];
var statusApplyingMoves = [
    "Grass Whistle", "Sleep Powder", "Lovely Kiss", "Spore"
];
var defrostingMoves = [
    "Burn Up", "Flame Wheel", "Flare Blitz", "Fusion Flare", "Pyro Ball", "Sacred Fire", "Scald", "Scorching Sands", "Steam Eruption"
];
function isNamed(moveName) {
    var names = [];
    for (var _i = 1; _i < arguments.length; _i++) {
        names[_i - 1] = arguments[_i];
    }
    return names.includes(moveName);
}
function isTrapping(move) {
    return isNamed.apply(void 0, __spreadArray([move.name], __read(trappingMoveNames), false));
}
function isTrappingStr(s) {
    return isNamed.apply(void 0, __spreadArray([s], __read(trappingMoveNames), false));
}
function isHighCritRate(s) {
    return isNamed.apply(void 0, __spreadArray([s], __read(highCritRatioMoveNames), false));
}
function isTwoHit(move) {
    return isNamed.apply(void 0, __spreadArray([move.name], __read(twoHitMoves), false));
}
function isThreeHit(move) {
    return isNamed.apply(void 0, __spreadArray([move.name], __read(threeHitMoves), false));
}
function movesetHasMove(moves, moveName) {
    var e_1, _a;
    try {
        for (var moves_1 = __values(moves), moves_1_1 = moves_1.next(); !moves_1_1.done; moves_1_1 = moves_1.next()) {
            var move = moves_1_1.value;
            if (move.move.name == moveName) {
                return true;
            }
        }
    }
    catch (e_1_1) { e_1 = { error: e_1_1 }; }
    finally {
        try {
            if (moves_1_1 && !moves_1_1.done && (_a = moves_1["return"])) _a.call(moves_1);
        }
        finally { if (e_1) throw e_1.error; }
    }
    return false;
}
function movesetHasMoves(moves) {
    var e_2, _a;
    var moveNames = [];
    for (var _i = 1; _i < arguments.length; _i++) {
        moveNames[_i - 1] = arguments[_i];
    }
    var hasMove = false;
    try {
        for (var moveNames_1 = __values(moveNames), moveNames_1_1 = moveNames_1.next(); !moveNames_1_1.done; moveNames_1_1 = moveNames_1.next()) {
            var moveName = moveNames_1_1.value;
            hasMove = movesetHasMove(moves, moveName);
            if (hasMove) {
                return true;
            }
        }
    }
    catch (e_2_1) { e_2 = { error: e_2_1 }; }
    finally {
        try {
            if (moveNames_1_1 && !moveNames_1_1.done && (_a = moveNames_1["return"])) _a.call(moveNames_1);
        }
        finally { if (e_2) throw e_2.error; }
    }
    return false;
}
function movesetHasSoundMove(moves) {
    return movesetHasMoves.apply(void 0, __spreadArray([moves], __read(soundMoves), false));
}
function movesetHasHighCritRatioMove(moves) {
    return movesetHasMoves.apply(void 0, __spreadArray([moves], __read(highCritRatioMoveNames), false));
}
function getMoveIndexesOfType(moves, type) {
    var e_3, _a;
    var moveIndexes = [];
    var i = 0;
    try {
        for (var moves_2 = __values(moves), moves_2_1 = moves_2.next(); !moves_2_1.done; moves_2_1 = moves_2.next()) {
            var move = moves_2_1.value;
            if (move.move.type == type) {
                moveIndexes.push(i);
            }
            i++;
        }
    }
    catch (e_3_1) { e_3 = { error: e_3_1 }; }
    finally {
        try {
            if (moves_2_1 && !moves_2_1.done && (_a = moves_2["return"])) _a.call(moves_2);
        }
        finally { if (e_3) throw e_3.error; }
    }
    return moveIndexes;
}
function movesetHasMultiHitMove(moves) {
    return movesetHasMoves.apply(void 0, __spreadArray([moves], __read(twoHitMoves), false)) || movesetHasMoves.apply(void 0, __spreadArray([moves], __read(threeHitMoves), false)) ||
        movesetHasMove(moves, "Triple Axel");
}
function getMultiHitCount(move) {
    if (isTwoHit(move)) {
        return 2;
    }
    if (isThreeHit(move)) {
        return move.ability == "Skill Link" ? 5 : 3;
    }
    return 1;
}
function getTripleAxelDamage(res) {
    var e_4, _a;
    var tripleAxelDamageRolls = [];
    var tripleAxelDamage = [];
    var i = 0;
    try {
        for (var _b = __values([20, 40, 60]), _c = _b.next(); !_c.done; _c = _b.next()) {
            var bp = _c.value;
            var move = res.move.clone();
            move.bp = bp;
            move.hits = 1;
            move.name = "Ice Punch";
            move.originalName = "Ice Punch";
            move.overrides = {
                basePower: bp,
                type: "Ice",
                category: "Physical"
            };
            tripleAxelDamageRolls[i] = (0, gen789_1.calculateSMSSSV)(_1.Generations.get(8), res.attacker.clone(), res.defender.clone(), move, res.field ? res.field.clone() : new field_1.Field())
                .damageRolls();
            i++;
        }
    }
    catch (e_4_1) { e_4 = { error: e_4_1 }; }
    finally {
        try {
            if (_c && !_c.done && (_a = _b["return"])) _a.call(_b);
        }
        finally { if (e_4) throw e_4.error; }
    }
    tripleAxelDamage = tripleAxelDamageRolls.reduce(function (acc, curr) {
        return acc.map(function (val, i) { return val + curr[i]; });
    });
    return tripleAxelDamage;
}
function getAIDeadAfterShellSmash(res, playerMaxDamage) {
    var e_5, _a;
    var aiCurrentHp = res[1][0].attacker.originalCurHP;
    var aiItem = res[1][0].attacker.item;
    var aiSlower = res[0][0].attacker.spe > res[0][0].defender.spe;
    var playerMoves = res[0];
    if (aiItem == "Focus Sash" && aiCurrentHp === res[1][0].attacker.maxHP()) {
        return false;
    }
    if (aiItem == "White Herb" || aiSlower) {
        return playerMaxDamage >= aiCurrentHp;
    }
    var playerMaxDamageAfterSS = 0;
    var defender = playerMoves[0].defender.clone();
    defender.boosts.atk += 2;
    defender.boosts.spa += 2;
    defender.boosts.spe += 2;
    defender.boosts.def -= 1;
    defender.boosts.spd -= 1;
    try {
        for (var playerMoves_1 = __values(playerMoves), playerMoves_1_1 = playerMoves_1.next(); !playerMoves_1_1.done; playerMoves_1_1 = playerMoves_1.next()) {
            var move = playerMoves_1_1.value;
            var maxRoll = Math.max.apply(Math, __spreadArray([], __read((0, gen789_1.calculateSMSSSV)(_1.Generations.get(8), move.attacker.clone(), defender.clone(), move.move, move.field ? move.field.clone() : new field_1.Field())
                .damageRolls()), false));
            if (maxRoll > playerMaxDamageAfterSS) {
                playerMaxDamageAfterSS = maxRoll;
            }
        }
    }
    catch (e_5_1) { e_5 = { error: e_5_1 }; }
    finally {
        try {
            if (playerMoves_1_1 && !playerMoves_1_1.done && (_a = playerMoves_1["return"])) _a.call(playerMoves_1);
        }
        finally { if (e_5) throw e_5.error; }
    }
    return playerMaxDamageAfterSS >= aiCurrentHp;
}
function getMoveIsStatus(moveName, moveBp) {
    return moveBp <= 0 &&
        !isNamed.apply(void 0, __spreadArray([moveName], __read(zeroBPButNotStatus), false));
}
exports.getMoveIsStatus = getMoveIsStatus;
function computeDistribution(array) {
    var sortedArray = array.sort(function (a, b) { return a - b; });
    var distribution = {};
    var totalCount = sortedArray.length;
    sortedArray.forEach(function (value) {
        if (!(value in distribution)) {
            distribution[value] = sortedArray.filter(function (v) { return v === value; }).length / totalCount;
        }
    });
    return distribution;
}
function objectEntriesIntKeys(obj) {
    return Object.entries(obj).map(function (_a) {
        var _b = __read(_a, 2), key = _b[0], value = _b[1];
        return [parseInt(key), value];
    });
}
function cartesian(arrays) {
    return arrays.reduce(function (acc, curr) {
        return acc.flatMap(function (d) { return curr.map(function (e) { return __spreadArray(__spreadArray([], __read(d), false), [e], false); }); });
    }, [[]]);
}
function setKeyStrings(keyString, splitKeys) {
    var keyStrings = [];
    for (var splitKey in splitKeys) {
        if (keyString.includes(splitKey)) {
            keyStrings.push.apply(keyStrings, __spreadArray([], __read(splitKeyString(keyString, splitKey)), false));
        }
    }
    if (keyStrings.length == 0) {
        keyStrings.push(keyString);
    }
    return keyStrings;
}
function splitKeyString(keyString, subString) {
    var keyStrings = [];
    var parts = keyString.split("/");
    var indices = parts.reduce(function (acc, part, i) {
        if (!part.includes(subString)) {
            acc.push(i);
        }
        return acc;
    }, []);
    for (var index in indices) {
        var newKeyString = __spreadArray([], __read(parts), false);
        newKeyString[index] = newKeyString[index].replace(subString, "0");
        keyStrings.push(newKeyString.map(String).join("/"));
    }
    return keyStrings;
}
function addOrUpdateProbability(probabilities, newKey, value) {
    var index = probabilities.findIndex(function (x) { return x.key === newKey; });
    if (index !== -1) {
        probabilities[index].value += value;
    }
    else {
        probabilities.push({ key: newKey, value: value });
    }
}
function processHighestDamage(key, prob, probabilities) {
    var hdIndex = key.indexOf("HD+");
    if (hdIndex === -1) {
        addOrUpdateProbability(probabilities, key, prob);
        return;
    }
    var sixKey = key.slice(0, hdIndex) + "6+" + key.slice(hdIndex + 3);
    processHighestDamage(sixKey, prob * 0.8, probabilities);
    var eightKey = key.slice(0, hdIndex) + "8+" + key.slice(hdIndex + 3);
    processHighestDamage(eightKey, prob * 0.2, probabilities);
}
function updateProbabilityWithVariance(probabilities, key, prob) {
    var e_6, _a;
    var matches = Array.from(key.matchAll(/HD\+\d+/g));
    if (matches.length === 0) {
        addOrUpdateProbability(probabilities, key, prob);
        return;
    }
    var combinations = [];
    processHighestDamage(key, 1, combinations);
    try {
        for (var combinations_1 = __values(combinations), combinations_1_1 = combinations_1.next(); !combinations_1_1.done; combinations_1_1 = combinations_1.next()) {
            var combination = combinations_1_1.value;
            var newKey = combination.key;
            var combinationFactor = combination.value;
            var keyChanged = false;
            var newKeyValues = newKey.split("/");
            for (var i = 0; i < newKeyValues.length; i++) {
                var newKeyValue = newKeyValues[i];
                var score = newKeyValue.split(":")[1];
                if (score.includes('+')) {
                    var parts = score.split('+').map(Number);
                    var sum = parts.reduce(function (acc, val) { return acc + val; }, 0);
                    newKeyValues[i] = newKeyValue.split(":")[0] + ":" + sum.toString();
                    keyChanged = true;
                }
            }
            if (keyChanged) {
                newKey = newKeyValues.join("/");
            }
            addOrUpdateProbability(probabilities, newKey, prob * combinationFactor);
        }
    }
    catch (e_6_1) { e_6 = { error: e_6_1 }; }
    finally {
        try {
            if (combinations_1_1 && !combinations_1_1.done && (_a = combinations_1["return"])) _a.call(combinations_1);
        }
        finally { if (e_6) throw e_6.error; }
    }
}
function getAISeesKill(moveScores, attackerAbility) {
    var e_7, _a, e_8, _b, e_9, _c;
    var abilityMoveBonus = attackerAbility === "Moxie" ||
        attackerAbility === "Beast Boost" ||
        attackerAbility === "Chilling Neigh" ||
        attackerAbility === "Grim Neigh";
    var killScores = [9, 11, 12, 14];
    var exceptionKillScores = [3, 6];
    if (abilityMoveBonus) {
        var newKs = [];
        var newEks = [];
        try {
            for (var killScores_1 = __values(killScores), killScores_1_1 = killScores_1.next(); !killScores_1_1.done; killScores_1_1 = killScores_1.next()) {
                var killScore = killScores_1_1.value;
                newKs.push(killScore + 1);
            }
        }
        catch (e_7_1) { e_7 = { error: e_7_1 }; }
        finally {
            try {
                if (killScores_1_1 && !killScores_1_1.done && (_a = killScores_1["return"])) _a.call(killScores_1);
            }
            finally { if (e_7) throw e_7.error; }
        }
        try {
            for (var exceptionKillScores_1 = __values(exceptionKillScores), exceptionKillScores_1_1 = exceptionKillScores_1.next(); !exceptionKillScores_1_1.done; exceptionKillScores_1_1 = exceptionKillScores_1.next()) {
                var exceptionKillScore = exceptionKillScores_1_1.value;
                newEks.push(exceptionKillScore + 1);
            }
        }
        catch (e_8_1) { e_8 = { error: e_8_1 }; }
        finally {
            try {
                if (exceptionKillScores_1_1 && !exceptionKillScores_1_1.done && (_b = exceptionKillScores_1["return"])) _b.call(exceptionKillScores_1);
            }
            finally { if (e_8) throw e_8.error; }
        }
        killScores = newKs;
        exceptionKillScores = newEks;
    }
    try {
        for (var moveScores_1 = __values(moveScores), moveScores_1_1 = moveScores_1.next(); !moveScores_1_1.done; moveScores_1_1 = moveScores_1.next()) {
            var moveStr = moveScores_1_1.value;
            var moveStrSplit = moveStr.split(':');
            var moveName = moveStrSplit[0];
            var moveScore = +moveStrSplit[1];
            if ((isNamed(moveName, "Relic Song", "Meteor Beam", "Future Sight")
                || isTrappingStr(moveName)) && exceptionKillScores.includes(moveScore)) {
                return true;
            }
            else if (killScores.includes(moveScore)) {
                return true;
            }
        }
    }
    catch (e_9_1) { e_9 = { error: e_9_1 }; }
    finally {
        try {
            if (moveScores_1_1 && !moveScores_1_1.done && (_c = moveScores_1["return"])) _c.call(moveScores_1);
        }
        finally { if (e_9) throw e_9.error; }
    }
    return false;
}
function getAiDeadToSecondaryDamage(result) {
    var currentHP = result.attacker.originalCurHP;
    var maxHP = result.attacker.stats.hp;
    var types = result.attacker.types;
    var ability = result.move.ability;
    var item = result.move.item;
    var status = result.attacker.status;
    var toxCounter = result.attacker.toxicCounter;
    var weather = result.field.weather;
    var statusDamage = 0;
    switch (status) {
        case "brn":
            statusDamage = Math.trunc(maxHP / 16);
            break;
        case "psn":
            statusDamage = Math.trunc(maxHP / 8);
            break;
        case "tox":
            statusDamage = Math.trunc(maxHP / 16) * toxCounter;
            break;
        default:
            break;
    }
    var weatherDamage = 0;
    switch (weather) {
        case "Sand":
            var immuneToSand = (types.includes("Rock") ||
                types.includes("Steel") ||
                types.includes("Ground")) ||
                (ability == "Sand Force" || ability == "Sand Rush" ||
                    ability == "Sand Veil" || ability == "Magic Guard" ||
                    ability == "Overcoat") ||
                item == "Safety Goggles";
            if (immuneToSand) {
                break;
            }
            weatherDamage = Math.trunc(maxHP / 16);
            break;
        case "Hail":
            var immuneToHail = types.includes("Ice") ||
                (ability == "Ice Body" || ability == "Snow Cloak" ||
                    ability == "Magic Guard" || ability == "Overcoat") ||
                item == "Safety Goggles";
            if (immuneToHail) {
                break;
            }
            weatherDamage = Math.trunc(maxHP / 16);
            break;
        default:
            break;
    }
    var damageTaken = statusDamage + weatherDamage;
    return damageTaken >= currentHP;
}
function shouldAIRecover(aiMon, recoveryPercentage, playerMaxRoll, aiFaster) {
    var aiMonCurrentHP = aiMon.originalCurHP;
    var aiMonMaxHP = aiMon.stats.hp;
    var aiHealthPercentage = Math.trunc((aiMonCurrentHP / aiMonMaxHP) * 100);
    var aiRecoveredHP = Math.trunc(aiMonMaxHP * recoveryPercentage);
    if (aiMon.status == "tox") {
        return 0;
    }
    if (playerMaxRoll >= aiRecoveredHP) {
        return 0;
    }
    if (aiFaster) {
        var playerCanKillAI = playerMaxRoll >= aiMonCurrentHP;
        var playerCanKillAIAfterRecovery = playerMaxRoll >= Math.min(aiMonCurrentHP + aiRecoveredHP, aiMonMaxHP);
        if (playerCanKillAI && !playerCanKillAIAfterRecovery) {
            return 1;
        }
        if (!playerCanKillAI) {
            if (aiHealthPercentage < 66 && aiHealthPercentage > 40) {
                return 0.5;
            }
            if (aiHealthPercentage <= 40) {
                return 1;
            }
        }
    }
    else {
        if (aiHealthPercentage < 50) {
            return 1;
        }
        if (aiHealthPercentage < 70) {
            return 0.75;
        }
    }
    return 0;
}
function isSuperEffective(move, monTypes, gravity, ringTarget) {
    if (gravity === void 0) { gravity = false; }
    if (ringTarget === void 0) { ringTarget = false; }
    var type1Effectiveness = (0, util_1.getMoveEffectiveness)(move.gen, move, monTypes[0], false, gravity, ringTarget);
    var type2Effectiveness = monTypes[1] != "" ?
        (0, util_1.getMoveEffectiveness)(move.gen, move, monTypes[1], false, gravity, ringTarget) :
        1;
    return (type1Effectiveness * type2Effectiveness) >= 2;
}
function updateMoveKVPWithMoveStrings(moveKVPs, moveStringToAdd) {
    var e_10, _a, e_11, _b, e_12, _c, e_13, _d;
    var newKvps = [];
    if (moveStringToAdd.score === 0 || moveStringToAdd.rate === 0) {
        return moveKVPs;
    }
    if (moveStringToAdd.rate === 1) {
        try {
            for (var moveKVPs_1 = __values(moveKVPs), moveKVPs_1_1 = moveKVPs_1.next(); !moveKVPs_1_1.done; moveKVPs_1_1 = moveKVPs_1.next()) {
                var moveKVP = moveKVPs_1_1.value;
                var key = "";
                var newKeyArr = [];
                var moveScoreStrings = moveKVP.key.split("/");
                try {
                    for (var moveScoreStrings_1 = (e_11 = void 0, __values(moveScoreStrings)), moveScoreStrings_1_1 = moveScoreStrings_1.next(); !moveScoreStrings_1_1.done; moveScoreStrings_1_1 = moveScoreStrings_1.next()) {
                        var moveScoreString = moveScoreStrings_1_1.value;
                        var moveScoreSplit = moveScoreString.split(":");
                        var moveName = moveScoreSplit[0];
                        var score = Number(moveScoreSplit[1]);
                        if (moveName === moveStringToAdd.move) {
                            score += moveStringToAdd.score;
                        }
                        newKeyArr.push("".concat(moveName, ":").concat(String(score)));
                    }
                }
                catch (e_11_1) { e_11 = { error: e_11_1 }; }
                finally {
                    try {
                        if (moveScoreStrings_1_1 && !moveScoreStrings_1_1.done && (_b = moveScoreStrings_1["return"])) _b.call(moveScoreStrings_1);
                    }
                    finally { if (e_11) throw e_11.error; }
                }
                key = newKeyArr.join("/");
                addOrUpdateProbability(newKvps, key, moveKVP.value);
            }
        }
        catch (e_10_1) { e_10 = { error: e_10_1 }; }
        finally {
            try {
                if (moveKVPs_1_1 && !moveKVPs_1_1.done && (_a = moveKVPs_1["return"])) _a.call(moveKVPs_1);
            }
            finally { if (e_10) throw e_10.error; }
        }
    }
    else {
        try {
            for (var moveKVPs_2 = __values(moveKVPs), moveKVPs_2_1 = moveKVPs_2.next(); !moveKVPs_2_1.done; moveKVPs_2_1 = moveKVPs_2.next()) {
                var moveKVP = moveKVPs_2_1.value;
                var oldKey = moveKVP.key;
                var key = "";
                var newKeyArr = [];
                var moveScoreStrings = moveKVP.key.split("/");
                try {
                    for (var moveScoreStrings_2 = (e_13 = void 0, __values(moveScoreStrings)), moveScoreStrings_2_1 = moveScoreStrings_2.next(); !moveScoreStrings_2_1.done; moveScoreStrings_2_1 = moveScoreStrings_2.next()) {
                        var moveScoreString = moveScoreStrings_2_1.value;
                        var moveScoreSplit = moveScoreString.split(":");
                        var moveName = moveScoreSplit[0];
                        var score = Number(moveScoreSplit[1]);
                        if (moveName === moveStringToAdd.move) {
                            score += moveStringToAdd.score;
                        }
                        newKeyArr.push("".concat(moveName, ":").concat(String(score)));
                    }
                }
                catch (e_13_1) { e_13 = { error: e_13_1 }; }
                finally {
                    try {
                        if (moveScoreStrings_2_1 && !moveScoreStrings_2_1.done && (_d = moveScoreStrings_2["return"])) _d.call(moveScoreStrings_2);
                    }
                    finally { if (e_13) throw e_13.error; }
                }
                key = newKeyArr.join("/");
                addOrUpdateProbability(newKvps, oldKey, moveKVP.value * (1 - moveStringToAdd.rate));
                addOrUpdateProbability(newKvps, key, moveKVP.value * moveStringToAdd.rate);
            }
        }
        catch (e_12_1) { e_12 = { error: e_12_1 }; }
        finally {
            try {
                if (moveKVPs_2_1 && !moveKVPs_2_1.done && (_c = moveKVPs_2["return"])) _c.call(moveKVPs_2);
            }
            finally { if (e_12) throw e_12.error; }
        }
    }
    return newKvps;
}
function calculateHighestDamage(moves) {
    var e_14, _a, e_15, _b, e_16, _c, e_17, _d, e_18, _e, e_19, _f, e_20, _g;
    var p1CurrentHealth = moves[0].defender.curHP();
    var arrays = moves.map(function (move) { return move.damageRolls().map(function (roll) { return Math.min(p1CurrentHealth, roll); }); });
    var aiFaster = moves[0].attacker.stats.spe >= moves[0].defender.stats.spe;
    var moveDistributions = arrays.map(function (array) { return computeDistribution(array); });
    var probabilities = [];
    var allChoices = cartesian(moveDistributions.map(function (distribution) { return objectEntriesIntKeys(distribution); }));
    try {
        for (var allChoices_1 = __values(allChoices), allChoices_1_1 = allChoices_1.next(); !allChoices_1_1.done; allChoices_1_1 = allChoices_1.next()) {
            var choice = allChoices_1_1.value;
            var keys = choice.map(function (_a) {
                var _b = __read(_a, 2), key = _b[0], value = _b[1];
                return key;
            });
            var moveProbabilities = choice.map(function (_a) {
                var _b = __read(_a, 2), key = _b[0], value = _b[1];
                return Number(value);
            });
            var keysForMaximumCheck = [1];
            var i = 0;
            try {
                for (var keys_1 = (e_15 = void 0, __values(keys)), keys_1_1 = keys_1.next(); !keys_1_1.done; keys_1_1 = keys_1.next()) {
                    var key = keys_1_1.value;
                    if (moves[i].move.category === "Status" ||
                        isNamed(moves[i].move.name, "Explosion", "Final Gambit", "Rollout", "Misty Explosion", "Self-Destruct", "Relic Song", "Meteor Beam", "Future Sight", "Counter", "Mirror Coat") ||
                        isTrapping(moves[i].move)) {
                        i++;
                        continue;
                    }
                    keysForMaximumCheck.push(key);
                    i++;
                }
            }
            catch (e_15_1) { e_15 = { error: e_15_1 }; }
            finally {
                try {
                    if (keys_1_1 && !keys_1_1.done && (_b = keys_1["return"])) _b.call(keys_1);
                }
                finally { if (e_15) throw e_15.error; }
            }
            var maximumKey = Math.max.apply(Math, __spreadArray([], __read(keysForMaximumCheck), false));
            var keyStrings = [];
            var keyString = "";
            i = 0;
            var highestDamageSet = false;
            try {
                for (var keys_2 = (e_16 = void 0, __values(keys)), keys_2_1 = keys_2.next(); !keys_2_1.done; keys_2_1 = keys_2.next()) {
                    var key = keys_2_1.value;
                    if (keyString != "") {
                        keyString += "/";
                    }
                    var moveName = moves[i].move.name;
                    var moveBonus = 0;
                    if (key >= p1CurrentHealth) {
                        if (aiFaster || moves[i].move.priority > 0) {
                            moveBonus += 6;
                        }
                        else {
                            moveBonus += 3;
                        }
                        if (moves[i].attacker.ability === "Moxie" ||
                            moves[i].attacker.ability === "Beast Boost" ||
                            moves[i].attacker.ability === "Chilling Neigh" ||
                            moves[i].attacker.ability === "Grim Neigh") {
                            moveBonus += 1;
                        }
                        if (moves[i].move.category === "Status" ||
                            isNamed(moves[i].move.name, "Explosion", "Final Gambit", "Rollout", "Misty Explosion", "Self-Destruct")) {
                            keyString += "".concat(moveName, ":0");
                            i++;
                            continue;
                        }
                        if (isNamed(moves[i].move.name, "Relic Song", "Meteor Beam", "Future Sight") || isTrapping(moves[i].move)) {
                            keyString += "".concat(moveName, ":").concat(moveBonus);
                            i++;
                            continue;
                        }
                    }
                    if (key === maximumKey && key >= p1CurrentHealth) {
                        keyString += "".concat(moveName, ":HD+").concat(moveBonus);
                    }
                    else if (key === maximumKey && !highestDamageSet) {
                        keyString += "".concat(moveName, ":HD+0");
                        highestDamageSet = true;
                    }
                    else {
                        keyString += "".concat(moveName, ":0");
                    }
                    i++;
                }
            }
            catch (e_16_1) { e_16 = { error: e_16_1 }; }
            finally {
                try {
                    if (keys_2_1 && !keys_2_1.done && (_c = keys_2["return"])) _c.call(keys_2);
                }
                finally { if (e_16) throw e_16.error; }
            }
            var probabilityOfChoice = 1;
            try {
                for (var moveProbabilities_1 = (e_17 = void 0, __values(moveProbabilities)), moveProbabilities_1_1 = moveProbabilities_1.next(); !moveProbabilities_1_1.done; moveProbabilities_1_1 = moveProbabilities_1.next()) {
                    var probability = moveProbabilities_1_1.value;
                    probabilityOfChoice *= Number(probability);
                }
            }
            catch (e_17_1) { e_17 = { error: e_17_1 }; }
            finally {
                try {
                    if (moveProbabilities_1_1 && !moveProbabilities_1_1.done && (_d = moveProbabilities_1["return"])) _d.call(moveProbabilities_1);
                }
                finally { if (e_17) throw e_17.error; }
            }
            keyStrings = setKeyStrings(keyString, ["HD"]);
            try {
                for (var keyStrings_1 = (e_18 = void 0, __values(keyStrings)), keyStrings_1_1 = keyStrings_1.next(); !keyStrings_1_1.done; keyStrings_1_1 = keyStrings_1.next()) {
                    var keyString_1 = keyStrings_1_1.value;
                    var probabilityToAdd = probabilityOfChoice / keyStrings.length;
                    addOrUpdateProbability(probabilities, keyString_1, probabilityToAdd);
                }
            }
            catch (e_18_1) { e_18 = { error: e_18_1 }; }
            finally {
                try {
                    if (keyStrings_1_1 && !keyStrings_1_1.done && (_e = keyStrings_1["return"])) _e.call(keyStrings_1);
                }
                finally { if (e_18) throw e_18.error; }
            }
        }
    }
    catch (e_14_1) { e_14 = { error: e_14_1 }; }
    finally {
        try {
            if (allChoices_1_1 && !allChoices_1_1.done && (_a = allChoices_1["return"])) _a.call(allChoices_1);
        }
        finally { if (e_14) throw e_14.error; }
    }
    var probabilitiesWithVariance = [];
    try {
        for (var probabilities_1 = __values(probabilities), probabilities_1_1 = probabilities_1.next(); !probabilities_1_1.done; probabilities_1_1 = probabilities_1.next()) {
            var probability = probabilities_1_1.value;
            if (probability.key.includes("HD")) {
                updateProbabilityWithVariance(probabilitiesWithVariance, probability.key, probability.value);
            }
            else {
                addOrUpdateProbability(probabilitiesWithVariance, probability.key, probability.value);
            }
        }
    }
    catch (e_19_1) { e_19 = { error: e_19_1 }; }
    finally {
        try {
            if (probabilities_1_1 && !probabilities_1_1.done && (_f = probabilities_1["return"])) _f.call(probabilities_1);
        }
        finally { if (e_19) throw e_19.error; }
    }
    if (!movesetHasHighCritRatioMove(moves)) {
        return probabilitiesWithVariance;
    }
    var critBoostMoveDist = [];
    var _loop_1 = function (damagingMoveDistKVP) {
        var e_21, _h, e_22, _j;
        var moveArr = damagingMoveDistKVP.key.split('/');
        var moveStringsToAdd = [];
        var moveKVPs = [
            {
                key: damagingMoveDistKVP.key,
                value: damagingMoveDistKVP.value
            }
        ];
        moveArr.forEach(function (moveScoreString, index) {
            var move = moves[index].move;
            if (isHighCritRate(move.name) && isSuperEffective(move, moves[index].defender.types, moves[0].field.isGravity, moves[index].defender.item == "Ring Target")) {
                moveStringsToAdd.push({ move: move.name, score: 1, rate: 0.5 });
            }
        });
        try {
            for (var moveStringsToAdd_1 = (e_21 = void 0, __values(moveStringsToAdd)), moveStringsToAdd_1_1 = moveStringsToAdd_1.next(); !moveStringsToAdd_1_1.done; moveStringsToAdd_1_1 = moveStringsToAdd_1.next()) {
                var moveStringToAdd = moveStringsToAdd_1_1.value;
                moveKVPs = updateMoveKVPWithMoveStrings(moveKVPs, moveStringToAdd);
            }
        }
        catch (e_21_1) { e_21 = { error: e_21_1 }; }
        finally {
            try {
                if (moveStringsToAdd_1_1 && !moveStringsToAdd_1_1.done && (_h = moveStringsToAdd_1["return"])) _h.call(moveStringsToAdd_1);
            }
            finally { if (e_21) throw e_21.error; }
        }
        try {
            for (var moveKVPs_3 = (e_22 = void 0, __values(moveKVPs)), moveKVPs_3_1 = moveKVPs_3.next(); !moveKVPs_3_1.done; moveKVPs_3_1 = moveKVPs_3.next()) {
                var moveKVP = moveKVPs_3_1.value;
                addOrUpdateProbability(critBoostMoveDist, moveKVP.key, moveKVP.value);
            }
        }
        catch (e_22_1) { e_22 = { error: e_22_1 }; }
        finally {
            try {
                if (moveKVPs_3_1 && !moveKVPs_3_1.done && (_j = moveKVPs_3["return"])) _j.call(moveKVPs_3);
            }
            finally { if (e_22) throw e_22.error; }
        }
    };
    try {
        for (var probabilitiesWithVariance_1 = __values(probabilitiesWithVariance), probabilitiesWithVariance_1_1 = probabilitiesWithVariance_1.next(); !probabilitiesWithVariance_1_1.done; probabilitiesWithVariance_1_1 = probabilitiesWithVariance_1.next()) {
            var damagingMoveDistKVP = probabilitiesWithVariance_1_1.value;
            _loop_1(damagingMoveDistKVP);
        }
    }
    catch (e_20_1) { e_20 = { error: e_20_1 }; }
    finally {
        try {
            if (probabilitiesWithVariance_1_1 && !probabilitiesWithVariance_1_1.done && (_g = probabilitiesWithVariance_1["return"])) _g.call(probabilitiesWithVariance_1);
        }
        finally { if (e_20) throw e_20.error; }
    }
    return critBoostMoveDist;
}
function generateMoveDist(damageResults, fastestSide, aiOptions) {
    var e_23, _a, e_24, _b;
    var _c;
    var moves = damageResults[1];
    var playerMoves = damageResults[0];
    var aiFaster = fastestSide != "0";
    var playerMon = moves[0].defender;
    var finalDist = [];
    moves.forEach(function (move, i) {
        finalDist[i] = 0.0;
    });
    if (movesetHasMultiHitMove(moves)) {
        moves.forEach(function (move, i) {
            var multiHit = getMultiHitCount(move.move);
            if (multiHit > 1) {
                if (typeof move.damage == 'number') {
                    move.damage *= multiHit;
                }
                else if (Array.isArray(move.damage)) {
                    move.damage = move.damage.map(function (x) { return x * multiHit; });
                }
            }
            if (move.move.name == "Triple Axel" && Array.isArray(move.damage)) {
                move.damage = getTripleAxelDamage(move);
            }
        });
    }
    var damagingMoveDist = calculateHighestDamage(moves);
    var playerHighestRoll = 0;
    damageResults[0].forEach(function (move, i) {
        var playerDamageRoll = typeof move.damage === 'number' ? move.damage : move.damage[move.damage.length - 1];
        if (movesetHasMultiHitMove(playerMoves)) {
            var multiHit = getMultiHitCount(move.move);
            if (multiHit > 1) {
                playerDamageRoll *= multiHit;
            }
        }
        if (move.move.isCrit) {
            playerDamageRoll = Math.trunc(playerDamageRoll / 1.5);
            if (move.attacker.ability == "Sniper") {
                playerDamageRoll = Math.trunc(playerDamageRoll / 1.5);
            }
        }
        if (playerDamageRoll > playerHighestRoll) {
            playerHighestRoll = playerDamageRoll;
        }
    });
    var aiDeadToPlayer = playerHighestRoll >= moves[0].attacker.originalCurHP &&
        !((moves[0].move.ability == "Sturdy" || moves[0].move.item == "Focus Sash") &&
            moves[0].attacker.originalCurHP == moves[0].attacker.stats.hp);
    var aiTwoHitKOd = playerHighestRoll * 2 >= moves[0].attacker.originalCurHP;
    var aiThreeHitKOd = playerHighestRoll * 3 >= moves[0].attacker.originalCurHP;
    var playerHasStatusCond = playerMon.status != "";
    var aiStatusCond = (_c = moves[0].attacker.status) !== null && _c !== void 0 ? _c : "";
    var playerTypes = playerMon.types;
    var playerAbility = moves[0].defender.moves[0].ability;
    var aiAbility = moves[0].move.ability;
    var playerHealthPercentage = Math.trunc((moves[0].defender.originalCurHP / moves[0].defender.stats.hp) * 100);
    var aiHealthPercentage = Math.trunc((moves[0].attacker.originalCurHP / moves[0].attacker.stats.hp) * 100);
    var aiMaxedOutAttack = moves[0].attacker.boosts.atk == 6;
    var aiMonName = moves[0].attacker.name;
    var aiItem = moves[0].attacker.item;
    var playerSideSpikes = moves[0].field.defenderSide.spikes > 0;
    var playerSideTSpikes = moves[0].field.defenderSide.tspikes > 0;
    var playerSideStealthRocks = moves[0].field.defenderSide.isSR;
    var aiReflect = moves[0].field.attackerSide.isReflect;
    var aiLightScreen = moves[0].field.attackerSide.isLightScreen;
    var aiHasTailwind = moves[0].field.attackerSide.isTailwind;
    var terrain = moves[0].field.terrain;
    var aiSlowerButFasterAfterPara = !aiFaster && moves[0].attacker.stats.spe > Math.trunc(moves[0].defender.stats.spe / 4);
    var trickRoomUp = moves[0].field.isTrickRoom;
    var playerLeechSeeded = moves[0].field.defenderSide.isSeeded;
    var aiHasAnyStatRaised = Object.values(moves[0].attacker.boosts).some(function (value) { return value > 0; });
    var weather = moves[0].field.weather;
    var playerHasStatusMove = playerMoves.some(function (x) { return getMoveIsStatus(x.move.name, x.move.bp); });
    var aiHasStatusMove = moves.some(function (x) { return getMoveIsStatus(x.move.name, x.move.bp); });
    var playerIncapacitated = playerMon.status == "frz" || playerMon.status == "slp";
    var firstTurnOut = aiOptions["firstTurnOutAiOpt"];
    var suckerPunchUsedLastTurn = aiOptions["suckerPunchAiOpt"];
    var aiLastMonOut = aiOptions["lastMonAiOpt"];
    var playerLastMonOut = aiOptions["playerLastMonAiOpt"];
    var playerCharmedOrConfused = aiOptions["playerCharmedOrConfusedAiOpt"];
    var playerTaunted = aiOptions["tauntAiOpt"];
    var playerImprisoned = aiOptions["imprisonAiOpt"];
    var encoreIncentive = aiOptions["encoreAiOpt"];
    var playerFirstTurnOut = aiOptions["playerFirstTurnOutAiOpt"];
    var aiMagnetRisen = aiOptions["magnetRiseAiOpt"];
    var playerMagnetRisen = aiOptions["playerMagnetRisenAiOpt"];
    var playerGrounded = aiOptions["playerGroundedAiOpt"];
    var protectIncentive = aiOptions["protectIncentiveAiOpt"];
    var protectDisincentive = aiOptions["protectDisincentiveAiOpt"];
    var aiProtectLastTurn = aiOptions["protectLastAiOpt"];
    var aiProtectLastTwoTurns = aiOptions["protectLastTwoAiOpt"];
    var debugLogging = aiOptions["enableDebugLogging"];
    var postBoostsMoveDist = [];
    var _loop_2 = function (damagingMoveDistKVP) {
        var e_25, _d, e_26, _e, e_27, _f;
        var moveArr = damagingMoveDistKVP.key.split('/');
        var moveStringsToAdd = [];
        var moveKVPs = [
            {
                key: damagingMoveDistKVP.key,
                value: damagingMoveDistKVP.value
            }
        ];
        var aiSeesKill = getAISeesKill(moveArr, aiAbility);
        moveArr.forEach(function (moveScoreString, index) {
            var e_28, _a;
            var move = moves[index].move;
            var moveName = moveScoreString.split(':')[0];
            var moveScore = Number(moveScoreString.split(':')[1]);
            var damageRolls = moves[index].damageRolls();
            var highestRoll = Math.max.apply(Math, __spreadArray([], __read(damageRolls), false));
            var anyValidDamageRolls = damageRolls.reduce(function (a, b) { return a + b; }, 0) > 0;
            var currentMoveCanKill = highestRoll >= moves[index].defender.originalCurHP;
            var moveIsStatus = getMoveIsStatus(moveName, move.bp);
            var moveHasPriority = move.priority > 0 || (moveName == "Grassy Glide" && terrain == "Grassy");
            if (moveHasPriority && !aiFaster && aiDeadToPlayer && anyValidDamageRolls) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: 11,
                    rate: 1
                });
            }
            if (move.priority > 0 && terrain == "Psychic") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -40,
                    rate: 1
                });
            }
            if (isTrapping(move) && anyValidDamageRolls) {
                moveStringsToAdd.push.apply(moveStringsToAdd, [{
                        move: moveName,
                        score: 6,
                        rate: 1
                    },
                    {
                        move: moveName,
                        score: 2,
                        rate: 0.2
                    }]);
            }
            var isDamagingSpeedReducing = moveName == "Icy Wind" || moveName == "Electroweb" || moveName == "Rock Tomb"
                || moveName == "Mud Shot" || moveName == "Low Sweep" || moveName == "Bulldoze";
            if (isDamagingSpeedReducing && moveScore == 0 && anyValidDamageRolls) {
                if (playerAbility != "Contrary" && playerAbility != "Clear Body" && playerAbility != "White Smoke" && !aiFaster) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 5,
                        rate: 1
                    });
                }
            }
            if (isNamed(moveName, "Skitter Smack", "Trop Kick", "Snarl", "Mystical Fire", "Breaking Swipe") && moveScore == 0) {
                var affectedMoveType_1 = moveName == "Trop Kick" || moveName == "Breaking Swipe" ? "Physical" : "Special";
                var playerHasAnyOfCorrespondingSplit = playerMoves.some(function (x) { return x.move.category == affectedMoveType_1 &&
                    (x.move.bp > 0 || (zeroBPButNotStatus.includes(x.move.name) && x.move.name != "(No Move)")); });
                if (playerAbility != "Contrary" && playerAbility != "Clear Body" && playerAbility != "White Smoke" &&
                    playerHasAnyOfCorrespondingSplit &&
                    anyValidDamageRolls) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 5,
                        rate: 1
                    });
                }
            }
            if (moveName == "Acid Spray") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: 6,
                    rate: 1
                });
            }
            if (moveName == "Future Sight") {
                if (aiFaster && aiDeadToPlayer) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 8,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
            }
            if (moveName == "Relic Song") {
                if (aiMonName == "Meloetta-Pirouette") {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 10,
                        rate: 1
                    });
                }
            }
            if (moveName == "Sucker Punch" && suckerPunchUsedLastTurn) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -20,
                    rate: 0.5
                });
            }
            if (moveName == "Pursuit") {
                if (currentMoveCanKill) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 10,
                        rate: 1
                    });
                }
                else {
                    if (playerHealthPercentage < 20) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 10,
                            rate: 1
                        });
                    }
                    else if (playerHealthPercentage < 40) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 8,
                            rate: 0.5
                        });
                    }
                }
                if (aiFaster) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 3,
                        rate: 1
                    });
                }
            }
            if (moveName == "Fell Stinger" && !aiMaxedOutAttack && currentMoveCanKill) {
                if (aiFaster) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 21 - moveScore,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 15 - moveScore,
                        rate: 1
                    });
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: 2,
                    rate: 0.2
                });
            }
            if (moveName == "Rollout") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: 7,
                    rate: 1
                });
            }
            if (moveName == "Stealth Rock") {
                if (playerSideStealthRocks) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    if (firstTurnOut) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 8,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 6,
                            rate: 1
                        });
                    }
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 1,
                        rate: 0.75
                    });
                }
            }
            if (moveName == "Spikes" || moveName == "Toxic Spikes") {
                if (moveName == "Spikes" && moves[0].field.defenderSide.spikes >= 3 ||
                    moveName == "Toxic Spikes" && moves[0].field.defenderSide.tspikes >= 2) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    if (firstTurnOut) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 8,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 6,
                            rate: 1
                        });
                    }
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 1,
                        rate: 0.75
                    });
                    if ((moveName == "Spikes" && playerSideSpikes) ||
                        ((moveName == "Toxic Spikes") && playerSideTSpikes)) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: -1,
                            rate: 1
                        });
                    }
                }
            }
            if (moveName == "Sticky Web") {
                if (firstTurnOut) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 9,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: 3,
                    rate: 0.75
                });
            }
            if (moveName == "Protect" || moveName == "King's Shield" ||
                moveName == "Spiky Shield" || moveName == "Baneful Bunker" ||
                moveName == "Detect" || moveName == "Obstruct") {
                var aiDeadToSecondaryDamage = getAiDeadToSecondaryDamage(moves[0]);
                if (aiProtectLastTwoTurns || aiDeadToSecondaryDamage) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    var protectScore = 6;
                    var playerBurnedOrPoisoned = false;
                    var aiBurnedOrPoisoned = false;
                    if (aiStatusCond == "brn" ||
                        aiStatusCond == "psn" ||
                        aiStatusCond == "tox") {
                        aiBurnedOrPoisoned = true;
                    }
                    if (playerMon.status == "brn" ||
                        playerMon.status == "psn" ||
                        playerMon.status == "tox") {
                        playerBurnedOrPoisoned = true;
                    }
                    if (protectDisincentive || aiBurnedOrPoisoned) {
                        protectScore -= 2;
                    }
                    if (protectIncentive || playerBurnedOrPoisoned) {
                        protectScore++;
                    }
                    if (firstTurnOut) {
                        protectScore--;
                    }
                    moveStringsToAdd.push({
                        move: moveName,
                        score: protectScore,
                        rate: 1
                    });
                    if (aiProtectLastTurn) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: -20,
                            rate: 0.5
                        });
                    }
                }
            }
            if (moveName == "Imprison") {
                var playerMoveNames = playerMoves.map(function (x) { return x.move.name; });
                var movesInCommon = movesetHasMoves.apply(void 0, __spreadArray([moves], __read(playerMoveNames), false));
                if (!movesInCommon || playerImprisoned) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 9,
                        rate: 1
                    });
                }
            }
            if (moveName == "Baton Pass") {
                if (aiLastMonOut) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else if (moves[0].field.attackerSide.isSubstitute || aiHasAnyStatRaised) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 14,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 0,
                        rate: 1
                    });
                }
            }
            if (moveName == "Tailwind") {
                if (!aiHasTailwind) {
                    if (!aiFaster) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 9,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 5,
                            rate: 1
                        });
                    }
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
            }
            if (moveName == "Trick Room") {
                if (trickRoomUp) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    if (!aiFaster) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 10,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 5,
                            rate: 1
                        });
                    }
                }
            }
            if (moveName == "Fake Out") {
                if (firstTurnOut && (playerAbility != "Shield Dust" && playerAbility != "Inner Focus") && anyValidDamageRolls) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 9,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
            }
            if (isNamed(moveName, "Helping Hand", "Follow Me")) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -6,
                    rate: 1
                });
            }
            if (moveName == "Final Gambit") {
                if (aiFaster && moves[index].attacker.originalCurHP > moves[index].defender.originalCurHP) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 8,
                        rate: 1
                    });
                }
                else if (aiFaster && aiDeadToPlayer) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 7,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
            }
            if (moveName.endsWith(" Terrain")) {
                if (!terrain) {
                    if (aiItem === "Terrain Extender") {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 9,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 8,
                            rate: 1
                        });
                    }
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
            }
            if (moveName == "Light Screen" || moveName == "Reflect") {
                if ((moveName == "Light Screen" && aiLightScreen) ||
                    ((moveName == "Reflect") && aiReflect)) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    var screenScore = 6;
                    var correspondingMoveSplit_1 = moveName == "Reflect" ? "Physical" : "Special";
                    var playerHasAnyOfCorrespondingSplit = playerMoves.some(function (x) { return x.move.category == correspondingMoveSplit_1 &&
                        (x.move.bp > 0 || (zeroBPButNotStatus.includes(x.move.name) && x.move.name != "(No Move)")); });
                    if (playerHasAnyOfCorrespondingSplit) {
                        if (aiItem == "Light Clay") {
                            screenScore++;
                        }
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 1,
                            rate: 0.5
                        });
                    }
                    moveStringsToAdd.push({
                        move: moveName,
                        score: screenScore,
                        rate: 1
                    });
                }
            }
            if (moveName == "Substitute") {
                if (playerAbility == "Infiltrator" ||
                    aiHealthPercentage <= 50 ||
                    moves[0].field.attackerSide.isSubstitute) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    var subScore = 6;
                    if (playerMon.status == "slp") {
                        subScore += 2;
                    }
                    if (playerLeechSeeded && aiFaster) {
                        subScore += 2;
                    }
                    if (movesetHasSoundMove(playerMoves)) {
                        subScore -= 8;
                    }
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: subScore,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: -1,
                            rate: 0.5
                        }]);
                }
            }
            if (moveName == "Explosion" || moveName == "Self-Destruct" || moveName == "Misty Explosion") {
                var boomUseless = !anyValidDamageRolls || (aiLastMonOut && !playerLastMonOut);
                var aiHealthPercentage_1 = Math.trunc((moves[0].attacker.originalCurHP / moves[0].attacker.stats.hp) * 100);
                if (boomUseless) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else if (aiHealthPercentage_1 < 10) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 10,
                        rate: 1
                    });
                }
                else if (aiHealthPercentage_1 < 33) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 8,
                        rate: .7
                    });
                }
                else if (aiHealthPercentage_1 < 66) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 7,
                        rate: 0.5
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 7,
                        rate: 0.05
                    });
                }
                if (aiLastMonOut && playerLastMonOut) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -1,
                        rate: 1
                    });
                }
            }
            if (moveName == "Memento") {
                var aiHealthPercentage_2 = Math.trunc((moves[0].attacker.originalCurHP / moves[0].attacker.stats.hp) * 100);
                if (aiLastMonOut) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else if (aiHealthPercentage_2 < 10) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 16,
                        rate: 1
                    });
                }
                else if (aiHealthPercentage_2 < 33) {
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 6,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 8,
                            rate: 0.7
                        }]);
                }
                else if (aiHealthPercentage_2 < 66) {
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 6,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 7,
                            rate: 0.5
                        }]);
                }
                else {
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 6,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 7,
                            rate: 0.05
                        }]);
                }
            }
            var paralyzingMoves = ["Thunder Wave", "Stun Spore", "Nuzzle", "Glare"];
            if (paralyzingMoves.includes(moveName)) {
                var hexIndex = moves.findIndex(function (x) { return x.move.name === "Hex"; });
                var paraIncentive = aiSlowerButFasterAfterPara || hexIndex != -1 || playerCharmedOrConfused;
                if (playerHasStatusCond ||
                    (move.type == "Electric" && (playerTypes.includes("Ground"))) ||
                    (playerAbility == "Limber") ||
                    (playerTypes.includes("Electric"))) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else if (paraIncentive) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 8,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 7,
                        rate: 1
                    });
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: -1,
                    rate: 0.5
                });
            }
            if (moveName == "Will-O-Wisp") {
                if (playerHasStatusCond || playerTypes.findIndex(function (type) { return type == "Fire"; }) != -1) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                    var willOWispScore = 0;
                    var hexIndex = moves.findIndex(function (x) { return x.move.name === "Hex"; });
                    var physicalIndex = damageResults[0].findIndex(function (x) { return x.move.category === "Physical" && x.move.bp > 0; });
                    if (hexIndex !== -1) {
                        willOWispScore++;
                    }
                    if (physicalIndex !== -1) {
                        willOWispScore++;
                    }
                    moveStringsToAdd.push({
                        move: moveName,
                        score: willOWispScore,
                        rate: 0.37
                    });
                }
            }
            if (moveName == "Trick" || moveName == "Switcheroo") {
                if (aiItem == "Toxic Orb" || aiItem == "Flame Orb" || aiItem == "Black Sludge") {
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 6,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 1,
                            rate: 0.5
                        }]);
                }
                else {
                    if (aiItem == "Iron Ball" || aiItem == "Lagging Tail" || aiItem == "Sticky Barb") {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 7,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 5,
                            rate: 1
                        });
                    }
                }
            }
            if (moveName == "Yawn" || moveName == "Dark Void" || moveName == "Grass Whistle" || moveName == "Sing" || moveName == "Hypnosis") {
                var sleepPreventingAbility = playerAbility == "Insomnia" || playerAbility == "Vital Spirit" || playerAbility == "Sweet Veil";
                if (sleepPreventingAbility || playerHasStatusCond || terrain == "Electric" || terrain == "Misty") {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                    var sleepScore = 0;
                    if (!aiSeesKill) {
                        sleepScore++;
                        var dreamEaterIndex = moves.findIndex(function (x) { return x.move.name === "Dream Eater"; });
                        var nightmareIndex = moves.findIndex(function (x) { return x.move.name === "Nightmare"; });
                        var snoreIndex = playerMoves.findIndex(function (x) { return x.move.name == "Snore"; });
                        var sleepTalkIndex = playerMoves.findIndex(function (x) { return x.move.name == "Sleep Talk"; });
                        if ((dreamEaterIndex != -1 || nightmareIndex != -1) && (snoreIndex == -1 && sleepTalkIndex == -1)) {
                            sleepScore++;
                        }
                        var hexIndex = moves.findIndex(function (x) { return x.move.name === "Hex"; });
                        if (hexIndex != -1) {
                            sleepScore++;
                        }
                        moveStringsToAdd.push({
                            move: moveName,
                            score: sleepScore,
                            rate: 0.25
                        });
                    }
                }
            }
            if (isNamed(moveName, "Toxic", "Poison Gas", "Poison Powder")) {
                if (playerHasStatusCond ||
                    ((playerTypes.includes("Poison") || playerTypes.includes("Steel")) && moves[0].ability != "Corrosion")) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                    if (playerHealthPercentage > 20 && !aiSeesKill) {
                        var toxScore = 0;
                        if (playerHighestRoll == 0 &&
                            (movesetHasMoves(moves, "Hex", "Venom Drench") || moves[0].ability == "Merciless")) {
                            toxScore += 2;
                        }
                        moveStringsToAdd.push({
                            move: moveName,
                            score: toxScore,
                            rate: 0.38
                        });
                    }
                }
            }
            var isOffensiveSetup = false;
            var isDefensiveSetup = false;
            var isContrary = aiAbility == "Contrary";
            var actAsBulkUp = false;
            if (isNamed(moveName, "Power-Up Punch", "Swords Dance", "Howl", "Stuff Cheeks", "Barrier", "Acid Armor", "Iron Defense", "Cotton Guard", "Charge Beam", "Tail Glow", "Nasty Plot", "Cosmic Power", "Bulk Up", "Calm Mind", "Dragon Dance", "Coil", "Hone Claws", "Quiver Dance", "Shift Gear", "Shell Smash", "Growth", "Work Up", "Curse", "No Retreat")) {
                if (aiDeadToPlayer ||
                    ((moveName != "Power-Up Punch" && moveName != "Swords Dance" && moveName != "Howl") &&
                        playerAbility == "Unaware")) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
            }
            if (moveScore == 0 && isContrary) {
                if (isContrary) {
                    if (isNamed(moveName, "Overheat", "Leaf Storm")) {
                        isOffensiveSetup = true;
                    }
                    else if (moveName == "Superpower" && anyValidDamageRolls) {
                        actAsBulkUp = true;
                    }
                }
            }
            else if (moveScore == 0) {
                if (isNamed.apply(void 0, __spreadArray([moveName], __read(offensiveSetup), false))) {
                    isOffensiveSetup = !(isNamed(moveName, "Charge Beam", "Power-Up Punch") &&
                        !anyValidDamageRolls);
                }
                else if (isNamed.apply(void 0, __spreadArray([moveName], __read(defensiveSetup), false))) {
                    isDefensiveSetup = true;
                }
            }
            if (isNamed(moveName, "Coil", "Bulk Up", "Quiver Dance", "No Retreat", "Calm Mind") ||
                moveName == "Curse" && !moves[0].attacker.types.includes("Ghost") ||
                actAsBulkUp) {
                if (isNamed(moveName, "Coil", "Bulk Up", "No Retreat", "Curse") || actAsBulkUp) {
                    if (playerMoves.some(function (x) { return x.move.category == "Physical" && !getMoveIsStatus(x.move.name, x.move.bp); }) &&
                        !playerMoves.some(function (x) { return x.move.category == "Special" && !getMoveIsStatus(x.move.name, x.move.bp); })) {
                        isDefensiveSetup = true;
                    }
                    else {
                        isOffensiveSetup = true;
                    }
                }
                else {
                    if (playerMoves.some(function (x) { return x.move.category == "Special" && !getMoveIsStatus(x.move.name, x.move.bp); }) &&
                        !playerMoves.some(function (x) { return x.move.category == "Physical" && !getMoveIsStatus(x.move.name, x.move.bp); })) {
                        isDefensiveSetup = true;
                    }
                    else {
                        isOffensiveSetup = true;
                    }
                }
            }
            if (isOffensiveSetup) {
                var offensiveScore = 6;
                if (playerIncapacitated) {
                    offensiveScore += 3;
                }
                if ((!aiFaster && aiTwoHitKOd) && !isContrary) {
                    offensiveScore -= 5;
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: offensiveScore,
                    rate: 1
                });
            }
            if (isDefensiveSetup) {
                var boostsDefAndSpDef = isNamed(moveName, "Stockpile", "Cosmic Power");
                var initialDefensiveScore = 6;
                if ((!aiFaster && aiTwoHitKOd) && !isContrary) {
                    initialDefensiveScore -= 5;
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: initialDefensiveScore,
                    rate: 1
                });
                var defensiveScore = 0;
                if (playerIncapacitated) {
                    defensiveScore += 2;
                }
                if (boostsDefAndSpDef && (moves[0].attacker.boosts.def < 2 || moves[0].attacker.boosts.spdef < 2)) {
                    defensiveScore += 2;
                }
                moveStringsToAdd.push.apply(moveStringsToAdd, [{
                        move: moveName,
                        score: defensiveScore,
                        rate: 0.95
                    }]);
            }
            if (moveName == "Agility" || moveName == "Rock Polish" || moveName == "Autotomize") {
                if (aiFaster) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 7,
                        rate: 1
                    });
                }
            }
            if (moveName == "Tail Glow" || moveName == "Nasty Plot" || moveName == "Work Up") {
                var score = 6;
                if (playerIncapacitated) {
                    score += 3;
                }
                else if (!aiThreeHitKOd) {
                    score += 1;
                    if (aiFaster) {
                        score++;
                    }
                }
                if (!aiFaster && aiTwoHitKOd) {
                    score -= 5;
                }
                if (moves[0].attacker.boosts.spatk >= 2) {
                    score--;
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: score,
                    rate: 1
                });
            }
            if (moveName == "Shell Smash") {
                var score = 6;
                if (playerIncapacitated) {
                    score += 3;
                }
                var aiDeadAfterShellSmash = getAIDeadAfterShellSmash(damageResults, playerHighestRoll);
                if ((aiFaster && !aiDeadAfterShellSmash) || (!aiFaster && !aiDeadToPlayer)) {
                    score += 2;
                }
                else {
                    score -= 2;
                }
                if (moves[0].attacker.boosts.atk >= 1 || moves[0].attacker.boosts.spatk >= 6) {
                    score -= 20;
                }
                moveStringsToAdd.push({
                    move: moveName,
                    score: score,
                    rate: 1
                });
            }
            if (moveName == "Belly Drum") {
                var sitrusRecovery = aiItem == "Sitrus Berry" ? Math.trunc(moves[0].attacker.stats.hp / 4) : 0;
                var hpAfterBellyDrum = moves[0].attacker.originalCurHP - Math.trunc(moves[0].attacker.stats.hp / 2) + sitrusRecovery;
                var aiNotDeadAfterBellyDrum = aiFaster ? (playerHighestRoll < hpAfterBellyDrum) : !aiDeadToPlayer;
                if (aiMaxedOutAttack || moves[0].attacker.originalCurHP - Math.trunc(moves[0].attacker.stats.hp / 2) <= 0) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else if (playerIncapacitated) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 9,
                        rate: 1
                    });
                }
                else if (aiNotDeadAfterBellyDrum) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 8,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 4,
                        rate: 1
                    });
                }
            }
            if (moveName == "Focus Energy" || moveName == "Laser Focus") {
                var critIncentive = move.ability == "Super Luck" || move.ability == "Sniper"
                    || move.item == "Scope Lens" || movesetHasHighCritRatioMove(moves);
                if ((moveName == "Focus Energy" && moves[0].field.attackerSide.isFocusEnergy) ||
                    playerAbility == "Shell Armor" || playerAbility == "Battle Armor" ||
                    aiDeadToPlayer) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    if (critIncentive) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 7,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 6,
                            rate: 1
                        });
                    }
                }
            }
            if (moveName == "Coaching") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -20,
                    rate: 1
                });
            }
            if (moveName == "Meteor Beam") {
                if (move.item == "Power Herb") {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 9,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
            }
            if (moveName == "Destiny Bond") {
                if (aiFaster && aiDeadToPlayer) {
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 6,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 1,
                            rate: 0.81
                        }]);
                }
                if (!aiFaster) {
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 5,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 1,
                            rate: 0.5
                        }]);
                }
            }
            var sunBasedHealingOverflow = false;
            var sunRecoveryRate = 0;
            if (isNamed(moveName, "Morning Sun", "Synthesis", "Moonlight")) {
                if (aiHealthPercentage == 100) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else if (aiHealthPercentage >= 85) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -6,
                        rate: 1
                    });
                }
                else {
                    if (weather == "Sun") {
                        sunRecoveryRate = shouldAIRecover(moves[0].attacker, 1, playerHighestRoll, aiFaster);
                    }
                    if (sunRecoveryRate == 1) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 7,
                            rate: 1
                        });
                    }
                    else {
                        sunBasedHealingOverflow = true;
                    }
                }
            }
            if (isNamed(moveName, "Recover", "Slack Off", "Heal Order", "Soft-Boiled", "Roost", "Strength Sap") || sunBasedHealingOverflow) {
                if (aiHealthPercentage == 100) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else if (aiHealthPercentage >= 85) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -6,
                        rate: 1
                    });
                }
                else {
                    var aiRecoverRate = shouldAIRecover(moves[0].attacker, 0.5, playerHighestRoll, aiFaster);
                    var sevenRate = sunRecoveryRate != 0 ?
                        sunRecoveryRate + ((1 - sunRecoveryRate) * aiRecoverRate) :
                        aiRecoverRate;
                    moveStringsToAdd.push.apply(moveStringsToAdd, [{
                            move: moveName,
                            score: 5,
                            rate: 1
                        },
                        {
                            move: moveName,
                            score: 2,
                            rate: sevenRate
                        }]);
                }
            }
            if (moveName == "Rest") {
                var restIncentive = aiItem == "Lum Berry" || aiItem == "Chesto Berry" ||
                    movesetHasMoves(moves, "Sleep Talk", "Snore") ||
                    moves[0].ability == "Shed Skin" || moves[0].ability == "Early Bird" ||
                    (moves[0].ability == "Hydration" && weather.includes("Rain")) ? 1 : 0;
                var aiShouldRecover = shouldAIRecover(moves[0].attacker, 1, playerHighestRoll, aiFaster);
                moveStringsToAdd.push.apply(moveStringsToAdd, [{
                        move: moveName,
                        score: 5,
                        rate: 1
                    },
                    {
                        move: moveName,
                        score: 2,
                        rate: aiShouldRecover
                    },
                    {
                        move: moveName,
                        score: 1,
                        rate: aiShouldRecover * restIncentive
                    }
                ]);
            }
            if (moveName == "Taunt") {
                if (playerTaunted) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    if ((movesetHasMove(playerMoves, "Trick Room") && !trickRoomUp) ||
                        movesetHasMove(playerMoves, "Defog") && moves[0].field.attackerSide.isAuroraVeil && aiFaster) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 9,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 5,
                            rate: 1
                        });
                    }
                }
            }
            if (moveName == "Encore") {
                if (playerFirstTurnOut) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    if (aiFaster && encoreIncentive) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 7,
                            rate: 1
                        });
                    }
                    else if (!aiFaster) {
                        moveStringsToAdd.push.apply(moveStringsToAdd, [{
                                move: moveName,
                                score: 5,
                                rate: 1
                            },
                            {
                                move: moveName,
                                score: 1,
                                rate: 0.5
                            }
                        ]);
                    }
                }
            }
            if (moveName == "Counter" || moveName == "Mirror Coat") {
                var playerImmune = (moveName == "Counter" && playerTypes.includes("Ghost")) ||
                    (moveName == "Mirror Coat" && playerTypes.includes("Dark"));
                var aiSturdyAndFullHP = (aiAbility == "Sturdy" || aiItem == "Focus Sash") && aiHealthPercentage == 100;
                var correspondingMoveSplit_2 = moveName == "Counter" ? "Physical" : "Special";
                var playerOnlyHasMovesOfCorrespondingSplit = playerMoves.every(function (x) { return x.move.category == correspondingMoveSplit_2 && x.move.bp > 0; });
                var playerNoMovesOfCorrespondingSplit = playerMoves.every(function (x) { return x.move.category != correspondingMoveSplit_2 || x.move.bp == 0; });
                if ((aiDeadToPlayer && !aiSturdyAndFullHP) ||
                    playerImmune ||
                    playerNoMovesOfCorrespondingSplit) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
                else {
                    var counterScore = 6;
                    if (playerHighestRoll >= moves[0].attacker.originalCurHP &&
                        (aiAbility == "Sturdy" || aiItem == "Focus Sash") &&
                        aiHealthPercentage == 100 &&
                        playerOnlyHasMovesOfCorrespondingSplit) {
                        counterScore += 2;
                    }
                    moveStringsToAdd.push({
                        move: moveName,
                        score: counterScore,
                        rate: 1
                    });
                    if (!aiDeadToPlayer && playerOnlyHasMovesOfCorrespondingSplit) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 2,
                            rate: 0.8
                        });
                    }
                    if (aiFaster) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: -1,
                            rate: 0.25
                        });
                    }
                    if (playerHasStatusMove) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: -1,
                            rate: 0.25
                        });
                    }
                }
            }
            if (moveName == "Magnet Rise") {
                if (aiMagnetRisen) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -40,
                        rate: 1
                    });
                }
                else {
                    var playerGroundMoveIndexes = getMoveIndexesOfType(playerMoves, "Ground");
                    var playerHasDamagingGroundMove = false;
                    try {
                        for (var playerGroundMoveIndexes_1 = (e_28 = void 0, __values(playerGroundMoveIndexes)), playerGroundMoveIndexes_1_1 = playerGroundMoveIndexes_1.next(); !playerGroundMoveIndexes_1_1.done; playerGroundMoveIndexes_1_1 = playerGroundMoveIndexes_1.next()) {
                            var groundMoveIndex = playerGroundMoveIndexes_1_1.value;
                            var groundMoveDamage = playerMoves[groundMoveIndex].damage;
                            if ((typeof groundMoveDamage === 'number' && groundMoveDamage != 0) ||
                                (Array.isArray(groundMoveDamage) && groundMoveDamage.reduce(function (a, b) { return a + b; }, 0) > 0)) {
                                playerHasDamagingGroundMove = true;
                                break;
                            }
                        }
                    }
                    catch (e_28_1) { e_28 = { error: e_28_1 }; }
                    finally {
                        try {
                            if (playerGroundMoveIndexes_1_1 && !playerGroundMoveIndexes_1_1.done && (_a = playerGroundMoveIndexes_1["return"])) _a.call(playerGroundMoveIndexes_1);
                        }
                        finally { if (e_28) throw e_28.error; }
                    }
                    if (aiFaster && playerHasDamagingGroundMove) {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 8,
                            rate: 1
                        });
                    }
                    else {
                        moveStringsToAdd.push({
                            move: moveName,
                            score: 5,
                            rate: 1
                        });
                    }
                }
            }
            if (moveName == "(No Move)") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -100,
                    rate: 1
                });
            }
            if (moveName == "Sleep Talk" && aiStatusCond != "slp") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -40,
                    rate: 1
                });
            }
            if (moveName == "Flame Charge"
                && moveScore == 0 && !aiFaster && anyValidDamageRolls) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: 6,
                    rate: 1
                });
            }
            if (isNamed.apply(void 0, __spreadArray([moveName], __read(powderMoves), false)) &&
                (playerTypes.includes("Grass") ||
                    playerAbility === "Overcoat" ||
                    moves[0].defender.item === "Safety Goggles")) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -50,
                    rate: 1
                });
            }
            if (playerHasStatusCond && isNamed.apply(void 0, __spreadArray([moveName], __read(statusApplyingMoves), false))) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -40,
                    rate: 1
                });
            }
            if (isNamed(moveName, "Leech Seed") &&
                (playerTypes.includes("Grass") || playerLeechSeeded)) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -20,
                    rate: 1
                });
            }
            if (moveName == "First Impression" && !firstTurnOut) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -50,
                    rate: 1
                });
            }
            var playerCanBeGrounded = playerTypes.includes("Flying") || playerAbility == "Levitate";
            if (isNamed(moveName, "Smack Down", "Thousand Arrows") && playerCanBeGrounded && !playerGrounded) {
                moveStringsToAdd.push({
                    move: moveName,
                    score: 6,
                    rate: 1
                });
            }
            if (weather == "Sun" && moveName == "Sunny Day" ||
                weather == "Rain" && moveName == "Rain Dance" ||
                weather == "Sand" && moveName == "Sandstorm" ||
                weather == "Hail" && moveName == "Hail") {
                moveStringsToAdd.push({
                    move: moveName,
                    score: -40,
                    rate: 1
                });
            }
            if (moveName == "Scary Face") {
                if (!aiFaster) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
                else {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: -20,
                        rate: 1
                    });
                }
            }
        });
        var i = 0;
        try {
            for (var moveArr_1 = (e_25 = void 0, __values(moveArr)), moveArr_1_1 = moveArr_1.next(); !moveArr_1_1.done; moveArr_1_1 = moveArr_1.next()) {
                var moveScoreString = moveArr_1_1.value;
                var move = moves[i].move;
                var moveName = moveScoreString.split(':')[0];
                var moveScore = Number(moveScoreString.split(':')[1]);
                var moveIsStatus = getMoveIsStatus(moveName, move.bp);
                if (moveScore == 0 &&
                    moveIsStatus &&
                    !(moveStringsToAdd.map(function (x) { return x.move; }).includes(moveName))) {
                    moveStringsToAdd.push({
                        move: moveName,
                        score: 6,
                        rate: 1
                    });
                }
                i++;
            }
        }
        catch (e_25_1) { e_25 = { error: e_25_1 }; }
        finally {
            try {
                if (moveArr_1_1 && !moveArr_1_1.done && (_d = moveArr_1["return"])) _d.call(moveArr_1);
            }
            finally { if (e_25) throw e_25.error; }
        }
        try {
            for (var moveStringsToAdd_2 = (e_26 = void 0, __values(moveStringsToAdd)), moveStringsToAdd_2_1 = moveStringsToAdd_2.next(); !moveStringsToAdd_2_1.done; moveStringsToAdd_2_1 = moveStringsToAdd_2.next()) {
                var moveStringToAdd = moveStringsToAdd_2_1.value;
                moveKVPs = updateMoveKVPWithMoveStrings(moveKVPs, moveStringToAdd);
            }
        }
        catch (e_26_1) { e_26 = { error: e_26_1 }; }
        finally {
            try {
                if (moveStringsToAdd_2_1 && !moveStringsToAdd_2_1.done && (_e = moveStringsToAdd_2["return"])) _e.call(moveStringsToAdd_2);
            }
            finally { if (e_26) throw e_26.error; }
        }
        try {
            for (var moveKVPs_4 = (e_27 = void 0, __values(moveKVPs)), moveKVPs_4_1 = moveKVPs_4.next(); !moveKVPs_4_1.done; moveKVPs_4_1 = moveKVPs_4.next()) {
                var moveKVP = moveKVPs_4_1.value;
                addOrUpdateProbability(postBoostsMoveDist, moveKVP.key, moveKVP.value);
            }
        }
        catch (e_27_1) { e_27 = { error: e_27_1 }; }
        finally {
            try {
                if (moveKVPs_4_1 && !moveKVPs_4_1.done && (_f = moveKVPs_4["return"])) _f.call(moveKVPs_4);
            }
            finally { if (e_27) throw e_27.error; }
        }
    };
    try {
        for (var damagingMoveDist_1 = __values(damagingMoveDist), damagingMoveDist_1_1 = damagingMoveDist_1.next(); !damagingMoveDist_1_1.done; damagingMoveDist_1_1 = damagingMoveDist_1.next()) {
            var damagingMoveDistKVP = damagingMoveDist_1_1.value;
            _loop_2(damagingMoveDistKVP);
        }
    }
    catch (e_23_1) { e_23 = { error: e_23_1 }; }
    finally {
        try {
            if (damagingMoveDist_1_1 && !damagingMoveDist_1_1.done && (_a = damagingMoveDist_1["return"])) _a.call(damagingMoveDist_1);
        }
        finally { if (e_23) throw e_23.error; }
    }
    if (debugLogging) {
        console.log(postBoostsMoveDist);
    }
    if (movesetHasMultiHitMove(moves)) {
        moves.forEach(function (move, i) {
            var multiHit = getMultiHitCount(move.move);
            if (multiHit > 1) {
                if (typeof move.damage == 'number') {
                    move.damage /= multiHit;
                }
                else if (Array.isArray(move.damage)) {
                    move.damage = move.damage.map(function (x) { return x / multiHit; });
                }
            }
        });
    }
    var _loop_3 = function (dist) {
        var moveArr = dist.key.split('/');
        var maxScore = 0;
        var moves_3 = [];
        moveArr.forEach(function (moveScoreString, index) {
            var scoreString = moveScoreString.split(':')[1];
            var score = Number(scoreString);
            if (score > maxScore) {
                maxScore = score;
                moves_3 = [];
                moves_3.push(index);
            }
            else if (score === maxScore) {
                moves_3.push(index);
            }
        });
        moves_3.forEach(function (move) {
            finalDist[move] += dist.value / moves_3.length;
        });
    };
    try {
        for (var postBoostsMoveDist_1 = __values(postBoostsMoveDist), postBoostsMoveDist_1_1 = postBoostsMoveDist_1.next(); !postBoostsMoveDist_1_1.done; postBoostsMoveDist_1_1 = postBoostsMoveDist_1.next()) {
            var dist = postBoostsMoveDist_1_1.value;
            _loop_3(dist);
        }
    }
    catch (e_24_1) { e_24 = { error: e_24_1 }; }
    finally {
        try {
            if (postBoostsMoveDist_1_1 && !postBoostsMoveDist_1_1.done && (_b = postBoostsMoveDist_1["return"])) _b.call(postBoostsMoveDist_1);
        }
        finally { if (e_24) throw e_24.error; }
    }
    return finalDist;
}
exports.generateMoveDist = generateMoveDist;
//# sourceMappingURL=ai.js.map