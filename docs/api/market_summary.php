<?php
/**
 * "오늘의 시황" 실시간 프록시.
 *
 * 기존엔 09:05 GitHub Actions 배치(update_open_price.py)가 하루 한 번 만든
 * market_summary를 정적 JSON에 구워 넣는 방식이었는데, 이 배치의 예약 실행이
 * 매일 3시간 이상 늦게 도는 문제(GitHub Actions 무료 요금제의 스케줄링 지연,
 * 특히 자정 UTC 부근이 몰림)가 반복돼서 오전 내내 "정보 없음"으로 보였다.
 *
 * 네이버 API/페이지는 브라우저의 교차 출처(Origin 헤더 있는) 요청을 403으로
 * 막기 때문에 프론트가 직접 호출할 수 없다(실제 curl로 확인함) - 그래서 같은
 * 출처인 이 PHP가 대신 서버 대 서버로 조회해서 프론트에 넘겨준다. 로직은
 * dart_top3.py의 fetch_kospi_market_summary()/fetch_sector_movers()/
 * fetch_market_headlines()를 그대로 PHP로 옮긴 것 - 두 쪽 다 실패해도
 * 나머지는 살아남도록 항목별로 독립적으로 처리한다(같은 안정성 원칙).
 *
 * 짧은 캐시(60초)를 둬서 방문자가 몰려도 네이버에 매번 새로 요청하지 않는다.
 */

header('Content-Type: application/json; charset=utf-8');

const CACHE_TTL_SEC = 60;
const CACHE_PATH = __DIR__ . '/_cache/market_summary.json';
const BULLISH_RATIO = 60.0;
const BEARISH_RATIO = 40.0;

function fetch_url(string $url, string $referer = ''): ?string
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
        CURLOPT_HTTPHEADER => $referer ? ["Referer: $referer"] : [],
    ]);
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return ($body !== false && $code === 200) ? $body : null;
}

function fetch_index_and_breadth(): ?array
{
    $basic_raw = fetch_url('https://m.stock.naver.com/api/index/KOSPI/basic');
    $integration_raw = fetch_url('https://m.stock.naver.com/api/index/KOSPI/integration');
    if (!$basic_raw || !$integration_raw) {
        return null;
    }
    $basic = json_decode($basic_raw, true);
    $integration = json_decode($integration_raw, true);
    if (!$basic || !$integration) {
        return null;
    }

    $index_price = (float)str_replace(',', '', (string)($basic['closePrice'] ?? 0));
    $index_change = (float)str_replace(',', '', (string)($basic['compareToPreviousClosePrice'] ?? 0));
    $index_change_pct = (float)str_replace(',', '', (string)($basic['fluctuationsRatio'] ?? 0));
    $direction = $index_change > 0 ? 'up' : ($index_change < 0 ? 'down' : 'flat');

    $updown = $integration['upDownStockInfo'] ?? [];
    $rise_count = (int)($updown['riseCount'] ?? 0) + (int)($updown['upperCount'] ?? 0);
    $fall_count = (int)($updown['fallCount'] ?? 0) + (int)($updown['lowerCount'] ?? 0);
    $flat_count = (int)($updown['steadyCount'] ?? 0);
    $total = $rise_count + $fall_count + $flat_count;
    $rise_ratio = $total > 0 ? ($rise_count / $total * 100) : 0.0;

    if ($rise_ratio >= BULLISH_RATIO) {
        $sentiment = '강세장';
    } elseif ($rise_ratio >= BEARISH_RATIO) {
        $sentiment = '혼조';
    } else {
        $sentiment = '약세장';
    }

    return [
        'index_price' => $index_price,
        'index_change' => $index_change,
        'index_change_pct' => $index_change_pct,
        'direction' => $direction,
        'rise_count' => $rise_count,
        'fall_count' => $fall_count,
        'flat_count' => $flat_count,
        'sentiment' => $sentiment,
    ];
}

function fetch_sector_movers(): ?array
{
    $raw = fetch_url('https://finance.naver.com/sise/sise_group.naver?type=upjong', 'https://finance.naver.com/');
    if (!$raw) {
        return null;
    }
    $html = iconv('EUC-KR', 'UTF-8//IGNORE', $raw);
    $pattern = '/sise_group_detail\.naver\?type=upjong&no=\d+">([^<]+)<\/a>.*?<span class="tah p11[^"]*">\s*([+-][\d.]+)%/s';
    if (!preg_match_all($pattern, $html, $matches, PREG_SET_ORDER)) {
        return null;
    }
    $sectors = array_map(fn($m) => ['name' => trim($m[1]), 'change_pct' => (float)$m[2]], $matches);
    if (!$sectors) {
        return null;
    }
    usort($sectors, fn($a, $b) => $b['change_pct'] <=> $a['change_pct']);
    return [
        'top_gainer_sector' => $sectors[0],
        'top_loser_sector' => end($sectors),
    ];
}

function fetch_headlines(int $count = 2): array
{
    $raw = fetch_url('https://finance.naver.com/news/mainnews.naver', 'https://finance.naver.com/');
    if (!$raw) {
        return [];
    }
    $html = iconv('EUC-KR', 'UTF-8//IGNORE', $raw);
    if (!preg_match_all('/class="articleSubject">\s*<a href="[^"]+">([^<]+)<\/a>/s', $html, $matches)) {
        return [];
    }
    return array_slice(array_map('trim', $matches[1]), 0, $count);
}

function build_market_summary(): ?array
{
    $summary = fetch_index_and_breadth();
    if ($summary === null) {
        return null;
    }
    $sectors = fetch_sector_movers();
    if ($sectors !== null) {
        $summary = array_merge($summary, $sectors);
    }
    $headlines = fetch_headlines();
    if ($headlines) {
        $summary['headlines'] = $headlines;
    }
    return $summary;
}

// 캐시가 신선하면 그걸 그대로 반환 - 방문자가 몰려도 네이버에 매번 새로 요청하지 않는다.
if (is_file(CACHE_PATH) && (time() - filemtime(CACHE_PATH)) < CACHE_TTL_SEC) {
    echo file_get_contents(CACHE_PATH);
    exit;
}

$summary = build_market_summary();
if ($summary === null) {
    http_response_code(502);
    echo json_encode(['error' => '시황 조회 실패'], JSON_UNESCAPED_UNICODE);
    exit;
}

$json = json_encode($summary, JSON_UNESCAPED_UNICODE);
@mkdir(dirname(CACHE_PATH), 0755, true);
@file_put_contents(CACHE_PATH, $json);  // 캐시 쓰기 실패해도(권한 등) 응답 자체는 정상 반환
echo $json;
