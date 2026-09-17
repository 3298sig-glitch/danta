<?php
/** 진단용 임시 스크립트 - 닷홈 서버에서 실제로 네이버 뉴스검색이 되는지 확인용.
 * 확인 끝나면 삭제한다. */
header('Content-Type: application/json; charset=utf-8');

function fetch_url(string $url): array
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
    ]);
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    return [$body, $code, $err];
}

[$news_body, $news_code, $news_err] = fetch_url('https://search.naver.com/search.naver?where=news&query=' . urlencode('미국'));
$tit_count = $news_body ? preg_match_all('/data-heatmap-target="\.tit"/', $news_body) : 0;

[$wti_body, $wti_code, $wti_err] = fetch_url('https://polling.finance.naver.com/api/realtime/marketindex/energy/CLcv1');
[$sp500_body, $sp500_code, $sp500_err] = fetch_url('https://polling.finance.naver.com/api/realtime/worldstock/index/.INX');

echo json_encode([
    'news' => [
        'http_code' => $news_code,
        'curl_error' => $news_err ?: null,
        'body_length' => $news_body ? strlen($news_body) : 0,
        'tit_pattern_count' => $tit_count,
        'body_snippet' => $news_body ? substr($news_body, 0, 200) : null,
    ],
    'wti' => [
        'http_code' => $wti_code,
        'curl_error' => $wti_err ?: null,
        'body' => $wti_body ? json_decode($wti_body, true) : null,
    ],
    'sp500' => [
        'http_code' => $sp500_code,
        'curl_error' => $sp500_err ?: null,
        'body' => $sp500_body ? json_decode($sp500_body, true) : null,
    ],
], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
