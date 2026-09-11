-- Load a media_subtitler .translation.ass as a positioned second track.
-- The embedded ARIB source remains primary and keeps mpv's native rendering.
local utils = require 'mp.utils'

local function load_translation()
    local video = mp.get_property('path', '')
    if video == '' or video:find('://', 1, true) then return end
    local path = video:gsub('%.[^./\\]+$', '') .. '.translation.ass'
    local file = io.open(path, 'r')
    if not file then return end
    local header = file:read(8192) or ''
    file:close()
    if not header:find('; Media Subtitler ARIB translation v1', 1, true) then return end
    local index = tonumber(header:match('; Source stream index: (%d+)'))
    if not index then
        mp.msg.warn('Translation overlay has no source stream index; re-extract the ARIB captions.')
        return
    end
    local source
    for _, track in ipairs(mp.get_property_native('track-list') or {}) do
        if track.type == 'sub' and not track.external and track['ff-index'] == index
            and track.codec == 'arib_caption' then
            source = track.id
            break
        end
    end
    if not source then
        mp.msg.warn('The ARIB source stream for this translation overlay is unavailable.')
        return
    end
    local absolute = utils.join_path(mp.get_property('working-directory', ''), path)
    local target
    for _, track in ipairs(mp.get_property_native('track-list') or {}) do
        if track.type == 'sub' and track.external and
            (track['external-filename'] == path or track['external-filename'] == absolute) then
            target = track.id
        end
    end
    if not target then
        local before = {}
        for _, track in ipairs(mp.get_property_native('track-list') or {}) do
            if track.type == 'sub' then before[track.id] = true end
        end
        local ok, err = pcall(mp.commandv, 'sub-add', path, 'auto')
        if not ok then mp.msg.error(tostring(err)); return end
        for _, track in ipairs(mp.get_property_native('track-list') or {}) do
            if track.type == 'sub' and not before[track.id] then target = track.id end
        end
    end
    if target then
        if mp.get_property_number('sid') ~= source then
            mp.set_property_number('sid', source)
        end
        if mp.get_property('secondary-sub-ass-override') ~= 'no' then
            mp.set_property('secondary-sub-ass-override', 'no')
        end
        mp.set_property_number('secondary-sub-scale', 1)
        mp.set_property_number('secondary-sub-delay', mp.get_property_number('sub-delay', 0))
        mp.set_property_native('secondary-sub-visibility', true)
        mp.set_property_number('secondary-sid', target)
        mp.msg.info('Keeping embedded ARIB captions; Chinese/target translation loaded underneath.')
    end
end

mp.register_event('file-loaded', load_translation)
mp.register_script_message('arib-translation-load', load_translation)
