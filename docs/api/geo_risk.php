<?php
/**
 * 지정학적 리스크 경고 배너 - 실시간 프록시.
 *
 * 뉴스 키워드 검출(조건 A) + 실제 시장 반응(조건 B)을 조합해 2단계 경고
 * 레벨을 판정한다. 조건 B는 두 지표를 함께 본다:
 *   - S&P500 전일 종가 기준 등락률(주 판단 기준) - 09:05 KST 시점엔 이미
 *     전날 밤 미국장이 완전히 마감된 확정치라 신뢰도가 높은 선행지표.
 *   - 코스피 등락률(보조 판단 기준) - 09:05는 한국 장이 막 개장한 직후라
 *     그 자체로는 변동성이 크고 신뢰도가 낮지만, "지금 실제로 반응하고
 *     있는지"를 보는 보조 확인 신호로 같이 본다(사용자 확정, 2026-09-09).
 *
 * market_summary.php와 같은 이유로 배치가 아니라 실시간 프록시로 구현한다 -
 * 09:05 배치(GitHub Actions 예약 실행)가 매일 3시간 이상 늦게 도는 문제를
 * 이 기능도 그대로 물려받지 않기 위함. 페이지 로드 시점에 이 서버가 직접
 * 조회해서 넘겨준다 - 네이버는 브라우저의 교차 출처 요청을 막기 때문에
 * 프론트가 직접 호출할 수 없다(market_summary.php와 동일).
 *
 * 뉴스/S&P500/코스피 조회는 각각 독립적으로 실패 처리한다 - 하나가 깨져도
 * 나머지 조건으로 판정은 계속 진행된다(그래도 아무것도 못 구했으면 'none').
 */

header('Content-Type: application/json; charset=utf-8');

const CACHE_TTL_SEC = 300;  // 키워드 10개를 매번 검색하는 비용이 있어 시황(60초)보다 여유 있게
const CACHE_PATH = __DIR__ . '/_cache/geo_risk.json';

const NEWS_COUNT_THRESHOLD = 3;         // 조건 A: 이 값 이상이면 뉴스 조건 충족
const SP500_DROP_THRESHOLD_PCT = -1.5;  // 조건 B(주): S&P500 전일 종가 대비 이 값(%) 이하로 하락
const KOSPI_DROP_THRESHOLD_PCT = -1.5;  // 조건 B(보조): 코스피 전일 대비 이 값(%) 이하로 급락

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
 * 기사 수를 센다(회사명 뉴스 필터와 동일 원칙). 여러 키워드에 동시에 걸리는
 * 기사는 URL 기준으로 한 번만 센다. */
function count_geo_news(): array
{
    $seen_urls = [];
    $matched_keywords = [];

    foreach (GEO_KEYWORDS as $keyword) {
        $url = 'https://search.naver.com/search.naver?where=news&query=' . urlencode($keyword);
        $raw = fetch_url($url);
        if ($raw === null) {
            continue;  // 이 키워드 하나 실패해도 나머지 키워드는 계속 진행
        }
        $pattern = '/<a nocr="1" href="([^"]+)"[^>]*data-heatmap-target="\.tit"[^>]*>'
            . '<span[^>]*>(.*?)<\/span>/s';
        if (!preg_match_all($pattern, $raw, $matches, PREG_SET_ORDER)) {
            continue;
        }
        $hit_this_keyword = false;
        foreach ($matches as $m) {
            $title = html_entity_decode(trim(strip_tags($m[2])), ENT_QUOTES | ENT_HTML5, 'UTF-8');
            if (strpos($title, $keyword) === false) {
                continue;
            }
            $seen_urls[$m[1]] = true;
            $hit_this_keyword = true;
        }
        if ($hit_this_keyword) {
            $matched_keywords[] = $keyword;
        }
    }

    return ['news_count' => count($seen_urls), 'matched_keywords' => $matched_keywords];
}

/** 조건 B(주): S&P500 전일 종가 기준 등락률. finance.naver.com/world/(해외증시)
 * 페이지는 지수 데이터를 별도 AJAX 없이 <script> 안에 JSON 형태로 그대로
 * 담고 있어서("var americaData = jindo.$H({...})"), 그 안의 "SPI@SPX" 항목의
 * rate 필드(이미 부호 있는 등락률로 계산돼 있음)를 정규식으로 바로 뽑는다.
 * WTI 때와 달리 방향을 별도로 역산할 필요가 없어 더 간단하고 안정적이다. */
function fetch_sp500_change_pct(): ?float
{
    $raw = fetch_url('https://finance.naver.com/world/');
    if ($raw === null) {
        return null;
    }
    // 이 페이지는 EUC-KR이 아니라 UTF-8이라(실제 확인함) 별도 인코딩 변환이 필요 없다.
    if (!preg_match('/"SPI@SPX":\{[^}]*"rate":(-?[\d.]+)/', $raw, $m)) {
        return null;
    }
    return (float)$m[1];
}

/** 조건 B-2: 코스피 지수 전일 대비 등락률. */
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
    $news = count_geo_news();
    $sp500_change_pct = fetch_sp500_change_pct();
    $kospi_change_pct = fetch_kospi_change_pct();

    $condition_a = $news['news_count'] >= NEWS_COUNT_THRESHOLD;
    $condition_b = ($sp500_change_pct !== null && $sp500_change_pct <= SP500_DROP_THRESHOLD_PCT)
        || ($kospi_change_pct !== null && $kospi_change_pct <= KOSPI_DROP_THRESHOLD_PCT);

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
        'sp500_change_pct' => $sp500_change_pct,
        'kospi_change_pct' => $kospi_change_pct,
        'condition_a' => $condition_a,
        'condition_b' => $condition_b,
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
