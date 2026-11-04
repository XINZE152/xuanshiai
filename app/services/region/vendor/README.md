# Vendored ip2region Python binding

- 来源：https://gitee.com/lionsoul/ip2region
- 版本：`v2.12.0`
- 文件：`binding/python/xdbSearcher.py`
- 许可证：Apache-2.0-style，见同目录 `LICENSE.md`
- 选择原因：单文件 `XdbSearcher` 绑定，无需新增 pip 依赖；`ip_offline.py` 直接按
  `vendor.xdbSearcher` 导入。

数据文件 `data/region/ip2region_v4.xdb` 来自同版本 `data/ip2region.xdb`，字段格式为
`国家|区域|省份|城市|ISP`，`REGION_IP_CITY_INDEX=2`。升级到 v3.x 的
`binding/python/ip2region` 包结构时，需要同步调整查询绑定，不能只替换 xdb。
