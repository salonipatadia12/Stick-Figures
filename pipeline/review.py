"""Human-readable script view for the approval gate (terminal and Claude Code)."""
import textwrap

from .models import ANCHORED_RECIPES


def script_view(episode, style=None, issues=()):
    wps = style.words_per_second if style else 2.7
    words = episode.word_count()
    approved = 'APPROVED' if episode.is_approved('script') else 'awaiting approval'
    out = [f'{episode.title or "(untitled)"}',
           f'{episode.topic}',
           f'style {episode.style} · {episode.aspect} · target {episode.target_length}s · '
           f'{len(episode.beats)} beats · {words} words ≈ {words / wps:.0f}s at {wps} w/s · script {approved}',
           f'Hook promise: {episode.hook_promise}', '']
    wrap = lambda text, indent: textwrap.fill(text, 96, initial_indent=indent, subsequent_indent=' ' * len(indent))
    for b in episode.beats:
        cast = ', '.join(b.shot.cast) or 'no characters'
        header = f'{b.id}  {b.role.upper():8} [{b.shot.framing} · {cast}]'
        if b.on_screen_text:
            header += f'  ON SCREEN: "{b.on_screen_text}"'
        out.append(header)
        out.append(wrap(f'"{b.narration}"', '    SAY   '))
        out.append(wrap(b.visual_intent, '    SHOW  '))
        motion = '; '.join(m.recipe + (f'@"{m.anchor}"' if m.anchor else '') + (f' ({m.detail})' if m.detail else '')
                           for m in b.shot.motion)
        if b.shot.transition != 'cut':
            motion += f'; enters with {b.shot.transition}'
        out.append(wrap(motion, '    MOVE  '))
        if b.shot.pose_variants:
            out.append(wrap(' / '.join(b.shot.pose_variants), '    POSES '))
        st = getattr(b.shot, 'staging', None)
        if st is not None:
            place = st.setting if st.setting != 'other' else f'other: {st.setting_detail}'
            bits = [place] + [f'{p.who} {p.pose}' for p in st.poses]
            bits += [f'{a.who} {a.verb}' + (f' {a.target}' if a.target else '') + f'@"{a.anchor}"' for a in st.actions]
            if st.props:
                bits.append('props: ' + ', '.join(p.name + (f' ({p.held_by})' if p.held_by else '') for p in st.props))
            out.append(wrap(' · '.join(bits), '    STAGE '))
        if b.sfx:
            out.append(wrap(', '.join(f'{s.name}@"{s.anchor}"' for s in b.sfx), '    SFX   '))
        notes = []
        if b.source_ids:
            notes.append('sources ' + ', '.join(b.source_ids))
        if b.illustrative_numbers:
            notes.append('numbers are an illustrative EXAMPLE, not a statistic')
        if notes:
            out.append(wrap('; '.join(notes), '    NOTE  '))
        out.append('')
    if episode.sources:
        out.append('Sources')
        for s in episode.sources:
            out.append(wrap(f'{s.claim}', f'  {s.id:4}'))
            out.append(f'        {s.url}')
        out.append('')
    if issues:
        out.append('Checks')
        out.extend(f'  {i}' for i in issues)
    else:
        out.append('Checks: all editorial rules pass.')
    return '\n'.join(out)


def anchored_share(episode):
    """Fraction of beats with a word-anchored visual change (used in summaries)."""
    if not episode.beats:
        return 0.0
    hits = sum(any(m.recipe in ANCHORED_RECIPES for m in b.shot.motion) for b in episode.beats)
    return hits / len(episode.beats)
