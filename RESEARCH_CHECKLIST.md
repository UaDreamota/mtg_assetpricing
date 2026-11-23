# What I’d check before sorting portfolios

I want to understand which prices and which cards enter each calculation before I sort names into portfolios. [Main write-up](README.md)

## Checks I ran for this version

| Done | Question | Where to inspect the answer |
|---|---|---|
| ✓ | Did reprints inflate the counts? I used one Oracle ID per design, then reconciled the seven disjoint color totals and the debut cohorts to 30,901. Multitype cards appear in each applicable row. | [Type × color counts](results/exploration/type-color-counts.csv) |
| ✓ | What does the average card ask a player to read? I counted words and Scryfall tags, included zeros, and reported means and medians by color and type. | [Color profiles](results/exploration/color-profiles.csv) · [Type profiles](results/exploration/type-profiles.csv) |
| ✓ | Do lands drive the color comparison? I calculated mean mana value with and without lands and counted nonlands at each mana value, with 7+ as the last bin. | [Mana curves](figures/mana-by-color.png) |
| ✓ | Did word counts grow even with parenthetical text removed? I repeated the debut-cohort comparison and calculated the creature-only series. | [Cohort table](results/exploration/design-eras.csv) |
| ✓ | How skewed are prices? I compared mean, median, quartiles and the share under a dollar, then broke prices out by first rarity. | [Distribution](figures/paper-price-distribution.png) · [Numeric summary](results/exploration/summary.json) |
| ✓ | Can I put a value on a set when the quote archive has gaps? I counted eligible and quoted names for 59 expansions, varied the coverage cutoff, and removed each set’s top five holdings. | [Set totals and coverage](results/exploration/expansion-baskets.csv) |
| ✓ | Does the set calculation agree with the original Ixalan file? An independent count in `xln_cards.csv` yielded the same 259 eligible names, 251 quotes and \$144.46 subtotal. | [Expansion table](results/exploration/expansion-baskets.csv) |
| ✓ | Did the strict dollar cutoff and ranking select the intended holdings? I cross-checked all 300 names, printing IDs and quotes against the later local holdings table. Rank 300 was \$24.35, rank 301 was \$24.30, and 148 exactly-one-dollar names were excluded. | [Ranked holdings](results/exploration/top-300-holdings.csv) |
| ✓ | Do the creature and long-text comparisons survive adjustment? I fit the three equations on the same names, graphed the uncertainty, varied the text cutoff, dropped lands and repeated the interaction fit at four quarter-ends. | [Coefficients](results/exploration/regression-coefficients.csv) · [Quarter-end comparison](figures/quarterly-price-check.png) |
| ✓ | Is 15 words a plausible dividing line? I printed sample sizes below the raw price comparisons in seven word bands. At 11–15 words, just 25 names had positive Standard exposure. | [Word-band counts and price quartiles](results/exploration/prices-by-word-count.csv) |

## What I’d do next, in order

### 1. Find out what the long-text comparison is picking up

- [ ] **Rebuild the comparison within the legal card pool.** Join dated Standard eligibility and bans to the monthly prices. Count legal-and-seen, legal-but-unseen, and ineligible names separately. Plot price by word band in the first two groups, with the number of cards under every point. Keep unknown legality as its own count.
- [ ] **Compare like with like.** Within legal cards, break the word-price plot out by first rarity and by creature status. Estimate a continuous word-count curve with the same supply controls. Show where both exposure groups actually have observations. This would tell me how much weight to put on a 61-card short-text comparison.
- [ ] **Open the cards at the extremes.** Within matched rarity, type and debut era, list cards with the largest positive and negative fitted price residuals. Look up their actual rules text, keywords and number of quoted printings. I’d like the README to explain a few named examples as well as a coefficient.
- [ ] **Check the calendar around rotation.** Plot the same cards’ prices, deck counts and exposure in the months around each documented Standard rotation. Keep the denominator of downloaded lists on the plot so a change in archive coverage is visible.

### 2. Ask what makes a binder expensive

- [ ] **Price the same binder two ways.** Within each expansion, keep only names with both a regular set-specific quote and a cheapest printing anywhere. Sum both prices on those same names, plot the difference against set age, and label the cards responsible for the biggest gaps.
- [ ] **Separate set size from chase cards.** Plot the covered subtotal against the number of names, then repeat after removing the top one, five and ten. Report the mean quote per remaining card. A large set can be expensive to complete even when it has no spectacular single.
- [ ] **Inspect the missing slots.** Tabulate unquoted names by set, rarity, split layout and release year. Check a sample against raw printing names and dates, especially for sets just below 90% coverage. This tests whether the threshold tends to lose whole types of cards.
- [ ] **Ask who owns the top-300 weight.** Group December holdings by Reserved List status, first-printing era, card type and number of sets printed. Show both share of names and share of dollars. Then rank the over-\$1 names by tournament exposure and report their overlap with the price-ranked 300.
- [ ] **Change the quote convention on fixed holdings.** Reprice the same names with a last-day quote, the seven-day median, and a 30-day median. Record the last quote date and number of dates alongside each large move. If ranks change, identify the name and printing that crossed the boundary.

### 3. Go further with color, words and mechanics

- [ ] **Color versus color identity.** Repeat the type-by-color table using Commander color identity. List the designs that move cells and split out colorless lands. This makes the distinction tangible for a player building a deck.
- [ ] **Mana value versus castability.** Among nonlands at the same mana value, use the public mana parser to count required colored pips. Plot the distributions for mono- and multicolor cards, with hybrid and variable costs identified separately.
- [ ] **How much word growth comes from the mix of designs?** Hold rarity and card-type shares fixed at an earlier cohort’s weights and recompute mean words by debut year. Compare ordinary single-face cards separately, then repeat with parentheses removed. Show the number of contributing cards in every cell.
- [ ] **Which abilities actually take up space?** Within creatures of the same color and mana value, compare listed keywords with and without their parenthetical reminders. Label mechanics that add the most words and inspect cards that grant an ability to something else. A keyword tag on a card needn’t describe that card’s own abilities.

## The handoff to return research

- [ ] **Freeze the investable universe at the formation date.** Record the eligible printing, price source, coverage rule and what happens if its next quote is missing. Compare equal-copy and equal-dollar holdings on those same names before comparing performance.
- [ ] **Verify the return target on a handful of reprints.** Follow the same printing over one and three months and compare its change with the change in the median across all editions. A newly covered premium printing can move that median even if every previously observed edition holds its price.
- [ ] **Then do the sorts.** Freeze the signal definitions, verify the signal predates the holding window, and display group sizes, turnover, missing next prices and quoted returns together. The December top 300 is a description of a collection on one date.
