<?php
/**
 * 지정학적 리스크 경고 배너 - 실시간 프록시.
 *
 * 뉴스 키워드 검출(조건 A) + 실제 시장 반응(조건 B)을 조합해 2단계 경고
 * 레벨을 판정한다. 조건 B는 세 지표를 OR로 함께 본다:
 *   - S&P500 전일 종가 기준 등락률(주 판단 기준) - 09:05 KST 시점엔 이미
 *     전날 밤 미국장이 완전히 마감된 확정치라 신뢰도가 높은 선행지표.
 *   - 코스피 등락률(보조 판단 기준) - 09:05는 한국 장이 막 개장한 직후라
 *     그 자체로는 변동성이 크고 신뢰도가 낮지만, "지금 실제로 반응하고
 *     있는지"를 보는 보조 확인 신호로 같이 본다.
 *   - WTI 국제유가 급등 - 지정학 리스크(중동 확전 등)는 유가 급등으로도
 *     시장에 반영되는 경우가 많아 세 번째 신호로 추가(2026-09-09).
 *
 * market_summary.php와 같은 이유로 배치가 아니라 실시간 프록시로 구현한다 -
 * 09:05 배치(GitHub Actions 예약 실행)가 매일 3시간 이상 늦게 도는 문제를
 * 이 기능도 그대로 물려받지 않기 위함. 페이지 로드 시점에 이 서버가 직접
 * 조회해서 넘겨준다 - 네이버는 브라우저의 교차 출처 요청을 막기 때문에
 * 프론트가 직접 호출할 수 없다(market_summary.php와 동일).
 *
 * 뉴스/S&P500/코스피/WTI 조회는 각각 독립적으로 실패 처리한다 - 하나가
 * 깨져도 나머지 조건으로 판정은 계속 진행된다(전부 실패하면 'none').
 */

header('Content-Type: application/json; charset=utf-8');

const CACHE_TTL_SEC = 300;  // 키워드 10개를 매번 검색하는 비용이 있어 시황(60초)보다 여유 있게
const CACHE_PATH = __DIR__ . '/_cache/geo_risk.json';

const NEWS_COUNT_THRESHOLD = 3;         // 조건 A: 이 값 이상이면 뉴스 조건 충족
const NEWS_DETAIL_LIMIT = 8;            // 뉴스 펼쳐보기에 담을 기사 상세 최대 건수
const SP500_DROP_THRESHOLD_PCT = -1.5;  // 조건 B: S&P500 전일 종가 대비 이 값(%) 이하로 하락
const KOSPI_DROP_THRESHOLD_PCT = -1.5;  // 조건 B: 코스피 전일 대비 이 값(%) 이하로 급락
const WTI_SURGE_THRESHOLD_PCT = 3.0;    // 조건 B: WTI 전일 대비 이 값(%) 이상 급등

const GEO_KEYWORDS = [
    '이란', '이스라엘', '미국', '전쟁', '확전',
    '호르무즈해협', '유가급등', '공습', '미사일', '제재',
];

function fetch_url(string $url, array $extra_headers = []): ?string
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
        CURLOPT_HTTPHEADER => $extra_headers,
    ]);
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return ($body !== false && $code === 200) ? $body : null;
}

/** 조건 A: 지정학 키워드별로 뉴스를 검색해서, 제목에 그 키워드가 정확히 포함된
 * 기사를 모은다(회사명 뉴스 필터와 동일 원칙). 여러 키워드에 동시에 걸리는
 * 기사는 URL 기준으로 한 번만 센다. 판정용 news_count는 전체 distinct 건수를
 * 그대로 쓰고, 화면에 펼쳐 보여줄 상세 목록은 NEWS_DETAIL_LIMIT건까지만 담는다.
 * 정확한 발행시각은 안정적으로 못 뽑아서(자주 바뀌는 해시 클래스 안에 있음),
 * "최신순"은 네이버 검색 자체의 반환 순서(관련도·최신성 혼합)로 근사한다. */
function fetch_geo_news(): array
{
    $seen = [];  // url => ['title'=>, 'summary'=>, 'source'=>, 'url'=>]
    $matched_keywords = [];

    foreach (GEO_KEYWORDS as $keyword) {
        $url = 'https://search.naver.com/search.naver?where=news&query=' . urlencode($keyword);
        $raw = fetch_url($url);
        if ($raw === null) {
            continue;  // 이 키워드 하나 실패해도 나머지 키워드는 계속 진행
        }

        $title_pattern = '/<a nocr="1" href="([^"]+)"[^>]*data-heatmap-target="\.tit"[^>]*>'
            . '<span[^>]*>(.*?)<\/span>/s';
        if (!preg_match_all($title_pattern, $raw, $titles, PREG_SET_ORDER)) {
            continue;
        }

        // 본문 요약(.body)은 일부 결과에만 붙어있어서, url -> 요약 맵을 미리 만들어두고
        // 있으면 붙이고 없으면 생략한다(제목만으로도 충분히 의미 전달됨).
        $summaries = [];
        $body_pattern = '/<a nocr="1" href="([^"]+)"[^>]*data-heatmap-target="\.body"[^>]*>'
            . '<span[^>]*>(.*?)<\/span>/s';
        if (preg_match_all($body_pattern, $raw, $bodies, PREG_SET_ORDER)) {
            foreach ($bodies as $b) {
                $summaries[$b[1]] = html_entity_decode(trim(strip_tags($b[2])), ENT_QUOTES | ENT_HTML5, 'UTF-8');
            }
        }

        $hit_this_keyword = false;
        foreach ($titles as $t) {
            $title = html_entity_decode(trim(strip_tags($t[2])), ENT_QUOTES | ENT_HTML5, 'UTF-8');
            if (strpos($title, $keyword) === false) {
                continue;
            }
            $article_url = $t[1];
            $hit_this_keyword = true;
            if (!isset($seen[$article_url])) {
                $seen[$article_url] = [
                    'title' => $title,
                    'summary' => $summaries[$article_url] ?? null,
                    'source' => str_replace('www.', '', (string)parse_url($article_url, PHP_URL_HOST)),
                    'url' => $article_url,
                ];
            }
        }
        if ($hit_this_keyword) {
            $matched_keywords[] = $keyword;
        }
    }

    return [
        'news_count' => count($seen),
        'matched_keywords' => $matched_keywords,
        'news' => array_slice(array_values($seen), 0, NEWS_DETAIL_LIMIT),
    ];
}

/** 조건 B: 국제유가(WTI) 전일 대비 등락률.
 *
 * 2026-09-17: 기존에 쓰던 finance.naver.com/marketindex/ 페이지가 네이버
 * 개편으로 stock.naver.com(React/Next.js SPA)로 302 리다이렉트되도록 바뀌어서
 * (curl은 리다이렉트를 안 따라가게 해뒀으므로 항상 실패) 값을 못 가져오고
 * 있었다. 그 새 페이지가 실제로 호출하는 실시간 API
 * (polling.finance.naver.com)를 브라우저 네트워크 탭으로 직접 확인해서
 * 교체 - 정적 HTML 파싱보다 오히려 더 간단하고 안정적인 순수 JSON API다. */
function fetch_wti_change_pct(): ?float
{
    $raw = fetch_url('https://polling.finance.naver.com/api/realtime/marketindex/energy/CLcv1');
    if ($raw === null) {
        return null;
    }
    $data = json_decode($raw, true);
    $pct = $data['datas'][0]['fluctuationsRatio'] ?? null;
    return $pct !== null ? (float)$pct : null;
}

/** 조건 B: S&P500 전일 종가 기준 등락률. WTI와 같은 이유(네이버 개편)로
 * finance.naver.com/world/ 대신 실제 네트워크 호출로 확인한 실시간 API를
 * 쓴다. 네이버 내부 코드가 ".INX"라 처음엔 못 찾았는데(".SPX"는 빈 결과),
 * 실제 브라우저 요청을 그대로 재현해서 알아냄. */
function fetch_sp500_change_pct(): ?float
{
    $raw = fetch_url('https://polling.finance.naver.com/api/realtime/worldstock/index/.INX');
    if ($raw === null) {
        return null;
    }
    $data = json_decode($raw, true);
    $pct = $data['datas'][0]['fluctuationsRatio'] ?? null;
    return $pct !== null ? (float)$pct : null;
}

/** 조건 B: 코스피 지수 전일 대비 등락률. */
function fetch_kospi_change_pct(): ?float
{
    $raw = fetch_url('https://m.stock.naver.com/api/index/KOSPI/basic');
    if ($raw === null) {
        return null;
    }
    $data = json_decode($raw, true);
    if (!$data || !isset($data['fluctuationsRatio'])) {
        return null;
    }
    return (float)str_replace(',', '', (string)$data['fluctuationsRatio']);
}

function build_geo_risk(): array
{
    $news = fetch_geo_news();
    $sp500_change_pct = fetch_sp500_change_pct();
    $kospi_change_pct = fetch_kospi_change_pct();
    $wti_change_pct = fetch_wti_change_pct();

    $sp500_hit = $sp500_change_pct !== null && $sp500_change_pct <= SP500_DROP_THRESHOLD_PCT;
    $kospi_hit = $kospi_change_pct !== null && $kospi_change_pct <= KOSPI_DROP_THRESHOLD_PCT;
    $wti_hit = $wti_change_pct !== null && $wti_change_pct >= WTI_SURGE_THRESHOLD_PCT;

    $condition_a = $news['news_count'] >= NEWS_COUNT_THRESHOLD;
    $condition_b = $sp500_hit || $kospi_hit || $wti_hit;

    if ($condition_a && $condition_b) {
        $level = 'warning';
    } elseif ($condition_a || $condition_b) {
        $level = 'caution';
    } else {
        $level = 'none';
    }

    return [
        'level' => $level,
        'news_count' => $news['news_count'],
        'matched_keywords' => $news['matched_keywords'],
        'news' => $news['news'],
        'sp500_change_pct' => $sp500_change_pct,
        'kospi_change_pct' => $kospi_change_pct,
        'wti_change_pct' => $wti_change_pct,
        'condition_a' => $condition_a,
        'condition_b' => $condition_b,
        'sp500_hit' => $sp500_hit,
        'kospi_hit' => $kospi_hit,
        'wti_hit' => $wti_hit,
    ];
}

if (is_file(CACHE_PATH) && (time() - filemtime(CACHE_PATH)) < CACHE_TTL_SEC) {
    echo file_get_contents(CACHE_PATH);
    exit;
}

$result = build_geo_risk();
$json = json_encode($result, JSON_UNESCAPED_UNICODE);
@mkdir(dirname(CACHE_PATH), 0755, true);
@file_put_contents(CACHE_PATH, $json);
echo $json;
