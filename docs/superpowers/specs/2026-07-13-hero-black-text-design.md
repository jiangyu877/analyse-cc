# Homepage Hero Black Text Design

## Design Read

This is a focused visual update to the operational dashboard homepage. The selected Hero remains the same component and keeps its wave artwork, layout, copy, metrics, and responsive behavior. Only its foreground text palette and the supporting background contrast change.

## Scope

- Change all text inside the homepage Hero to pure black (`#000`).
- Cover the kicker, title, descriptive note, metric labels, and metric values.
- Add a light neutral overlay behind the Hero content so black text remains readable over dark portions of the existing image.
- Change Hero metric divider lines to a dark translucent line suitable for the lighter surface.
- Preserve the top navigation, brand, decorative shapes, LineWaves animation, Bento navigation, downstream content, routes, and copy.
- Preserve the existing user change to `.gitignore` without modifying or reverting it.

## Visual Treatment

- Add a Hero-specific `--hero-ink: #000` token instead of changing the global `--home-text` token.
- Set the Hero base color, note, metric labels, and previously colored metric values to `var(--hero-ink)`.
- Use `rgba(247, 248, 246, 0.72)` for `.hero-shade`. This keeps the bitmap and animated waves visible while lifting dark areas enough for black text.
- Use `rgba(0, 0, 0, 0.24)` for Hero metric divider lines.
- Keep the green kicker rule as a non-text accent.

## Implementation Boundary

The change stays in `app/templates/dashboard.html`. No route, database, JavaScript, content, or component structure changes are required.

## Failure Handling

The Hero token has a literal black fallback through its root declaration. If the image or WebGL animation fails, the existing Hero background plus the light shade still provides a readable surface. Reduced-motion behavior remains unchanged.

## Verification

1. Add a failing template contract test for the Hero-specific black token, scoped text rules, light shade, and dark metric dividers.
2. Implement the smallest CSS change that satisfies the contract.
3. Run the focused dashboard tests and the full test suite.
4. Render authenticated desktop (`1440 x 1000`) and mobile (`390 x 844`) screenshots.
5. Confirm computed text color is `rgb(0, 0, 0)` for the kicker, title, note, metric labels, and all metric values.
6. Confirm the topbar and Bento palette remain unchanged, no text overlaps, and horizontal overflow remains zero.

## Acceptance Criteria

- Every visible Hero text element is black on desktop and mobile.
- Black text remains legible across the existing Hero artwork.
- No Hero text, metric, or decorative element overlaps another element.
- The topbar and Bento section retain their current dark palette.
- Existing automated tests and responsive browser checks pass.
