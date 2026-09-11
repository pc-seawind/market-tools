function TitlseSearchConfig() {
	//with default parameter
    this.sortDir = '0';
    this.sortByOptions = 'DateTime';
    this.lang = 'en';
	
	//search criteria
	this.category = '';
	this.market = titleSearchConfig.market;
	this.stockId = '';
	this.documentType = '';
	this.fromDate = '';
	this.to = '';
	this.title = '';
	this.searchType = '';
	this.t1code = '';
	this.t2Gcode = '';
	this.t2code = '';
	this.currLoadRecordCnt = '100';// the oringal records count for the first time. will update this after ajax call, no need to update
	
	//Config on the ajax 
	this.titleSearchServletPath = 'titleSearchServlet.do';
	this.loadMoreCnt = '100';//for user click load more 

	
	//The text inside the result table going to update
	this.lbl_releaseTime_en = 'Release Time';
	this.lbl_stockCode_en = 'Stock Code';
	this.lbl_StockName_en = 'Stock Short Name';
	this.lbl_Document_en = 'Document';
	this.lbl_dodWebpath_en = 'View Documents on Display';
	this.lbl_jsonFailMessage_en = 'Please refresh the webpage to get latest data: ';
	this.lbl_releaseTime_zh = '\u767C\u653E\u6642\u9593';
	this.lbl_stockCode_zh = '\u80a1\u4efd\u4EE3\u865F';
	this.lbl_StockName_zh = '\u80a1\u4efd\u7c21\u7a31';
	this.lbl_Document_zh = '\u6587\u4ef6';
	this.lbl_dodWebpath_zh = '\u67E5\u95B1\u5C55\u793A\u6587\u4EF6';
	this.lbl_jsonFailMessage_zh = '\u8acb\u91cd\u65b0\u8f09\u5165\u9801\u9762\u4ee5\u53d6\u5f97\u6700\u65b0\u8cc7\u6599: ';

}



function loadMore() {
	updateCurrLoadRecordCnt(this.loadMoreCnt);
	getTitleSearchCriteria();
	searchAgain();
}

function sortTable(input) { 
	var sortList = $('.search-page-container .table').data('tablesorter').sortList[0];
	var sortByOptions = sortList[0];
	var sortDir = sortList[1];
	
	if(sortDir == '0'){
		setSortDir('1');
	} else if (sortDir == '1'){
		setSortDir('0');
	}

	if(input == ('DateTime')){
		setSortByOption(input);
	} else if(input == ('StockCode')){
		setSortByOption(input);
	} else if(input == ('StockName')){
		setSortByOption(input);
	}
	getTitleSearchCriteria();
	searchAgain();
}

function sortByReleaseTimeAsc() {
	this.sortDir = '1';
    this.sortByOptions = 'DateTime';
	getTitleSearchCriteria();
	searchAgain();
}

function sortByReleaseTimeDesc() {
	this.sortDir = '0';
    this.sortByOptions = 'DateTime';
	getTitleSearchCriteria();
	searchAgain();
}

function sortByStockNameAsc() {
	this.sortDir = '1';
    this.sortByOptions = 'StockName';
	getTitleSearchCriteria();
	searchAgain();
}

function sortByStockNameDesc() {
	this.sortDir = '0';
    this.sortByOptions = 'StockName';
	getTitleSearchCriteria();
	searchAgain();
}

function sortByStockCodeAsc() {
	this.sortDir = '1';
    this.sortByOptions = 'StockCode';
	getTitleSearchCriteria();
	searchAgain();
}

function sortByStockCodeDesc() {
	this.sortDir = '0';
    this.sortByOptions = 'StockCode';
	getTitleSearchCriteria();
	searchAgain();
}

function searchAgain() {    	

		var sortBy = this.sortByOptions;
		var sortDir = this.sortDir;
		var lang = this.lang;			
		var category = this.category;
		var market = this.market;
		var stockId = this.stockId;
		var documentType = this.documentType;
		var fromDate = this.fromDate;
		var toDate = this.to;
		var title = this.title;
		var searchType = this.searchType;
		var t1code = this.t1code;
		var t2Gcode = this.t2Gcode;
		var t2code = this.t2code;
		var currLoadRecordCnt = this.currLoadRecordCnt;
		
		var titleSearchServletPath = this.titleSearchServletPath;
		var result = null;
		var recordloaded = '';
		

		$(function(){
		
		$.ajax({
			type: "GET",
			url: titleSearchServletPath,
			data: { sortDir: sortDir, 
					sortByOptions: sortBy, 
					category: category, 
					market: market, 
					stockId: stockId, 
					documentType: documentType, 
					fromDate: fromDate, 
					toDate: toDate, 
					title: title, 
					searchType: searchType, 
					t1code: t1code, 
					t2Gcode: t2Gcode, 
					t2code: t2code, 
					rowRange: currLoadRecordCnt, 
					lang : lang},
			success: function(responseData) {	
				
				//update current loaded rowRange
				setCurrLoadRecordCnt(responseData.rowRange);
				
				//clear result table
				clearTable();
				
				//update language
				setLang(responseData.lang);
				
				//update bottom text
				updateBottomText(responseData.loadedRecord, responseData.recordCnt, responseData.hasNextRow);
				
				//update record count
				recordloaded = getTotalRecordTxt(responseData.lang,responseData.recordCnt);
				$('.total-records').text(recordloaded);
				result = JSON.parse(responseData.result);
				
				// update result table
				$.each(result, function (index, object) {
					writeJsonObj(object,responseData.lang);

				});
				table_tooltip();
			},
			error: function (xhr, textStatus, errorThrown) {
				console.log(xhr);			
				
				if(xhr.status != 200){
					if(lang == 'E'){
						alert(lbl_jsonFailMessage_en);	
					}else {
						alert(lbl_jsonFailMessage_zh);	
					}
				}
			}
		});
	});
}


function writeJsonObj(object,lang){
		var webPath = "";
		var fileSize = "";
		var lbl_releaseTime = getRealseTimeLabel(lang);
		var lbl_stockCode = getStockCodeLabel(lang);
		var lbl_StockName = getStockNameLabel(lang);
		var lbl_Document = getDocumentLabel(lang);		
		var lbl_dodWebpath = getDodWebpathLabel(lang);
		
		if(object.DOD_WEB_PATH != ''){
			webPath = webPath + ' <span class="View-Documents">' +
					   '<a href="' +
					   object.DOD_WEB_PATH +
					   '"  target="_blank" class="View-Document-on-Display">' +
					   lbl_dodWebpath +
					   '</a></span> ';	   
		}
		
		if(object.FILE_TYPE == 'HTM'){
			fileSize = '(<span class="attachment_filesize">' +
					   object.FILE_INFO +
					   '</span>, ' +
					   object.FILE_TYPE +
					   ')';
		}else {
			fileSize = '(<span class="attachment_filesize">' +
					   object.FILE_INFO +
					   '</span>)' +
					   '<span class="' +
					   (object.FILE_TYPE ? object.FILE_TYPE.toLowerCase() : object.FILE_TYPE) +
					   '"></span>';
		}
				
		var htmlTxt = [
			'<tr>',
			'<td class="text-right release-time"> <span class="mobile-list-heading">',
			lbl_releaseTime + ': ',
			'</span>',
			object.DATE_TIME ,
			'</td>' ,
			'<td class="text-right stock-short-code"><span class="mobile-list-heading">',
			lbl_stockCode + ':',
			'</span>' ,
			object.STOCK_CODE ,
			'</td>',
			'<td class="stock-short-name"><span class="mobile-list-heading">',
			lbl_StockName + ':',
			'</span>',
			object.STOCK_NAME,
			'</td>',
			
			'<td>',
			'<span class="mobile-list-heading">',
			lbl_Document + ':',
			'</span>',
			'<div class="headline">',
			object.SHORT_TEXT ,
			'</div>' ,
			
			'<div class="doc-link">',
			'<a href="' ,
			object.FILE_LINK ,
			'" target="_blank">' ,
			object.TITLE ,
			'</a> ' ,
			fileSize,
			webPath,
			'</div>',
			'</td>', 
			'</tr>'].join('');
		
		$('.search-page-container table.table tbody').append(htmlTxt);
}

function updateBottomText(loadedRecord,recordCnt,hasNextRow){
	
	var loadMore = getLoadMore(this.lang);
	var showing = getShowing(this.lang,loadedRecord,recordCnt);
	var noRecordsFound = notFoundText(this.lang);
	
	if(loadedRecord != '0'){
		var htmlTxt = [
		'<div class="search-results__content-loadmore component-loadmore component-loadmore-no-options">',
		'<div class="component-loadmore__container">'].join('');
		
		if (hasNextRow){
			var htmlTxt = [ htmlTxt,
			'<div class="component-loadmore__dropdown-container">',
			'<ul>',
			'<li>',
			'<a href="javascript:loadMore();" class="component-loadmore__link component-loadmore__icon" load-data="100">',
			loadMore,
			'</a>',
			'</li>',
			'</ul>',
			'</div>'].join('');
		}
		var htmlTxt = [ htmlTxt,
		'<div class="component-loadmore-leftPart__container">',
		showing,
		'</div>',
		'</div>',
		'</div>',
		'<div class="component-loadmore-leftPart__container-Mobile">',
		showing,
		'</div>'].join('');
	} else if(loadedRecord == '0'){
		var htmlTxt = [
		'<div class="search-results__content-loadmore component-loadmore component-loadmore-no-options">',
		'<div class="component-loadmore__container">',
		'<div class="result_norecords">',
		noRecordsFound,
		'</div>',
		'</div>',
		'</div>'].join('');
	}
	
	$('#recordCountPanel2').append(htmlTxt); 
}


function setSortDir(dir){
	this.sortDir = dir;
}

function setSortByOption(sortByOption){
	this.sortByOptions = sortByOption;
}

function setCategory(category){
	this.category = category;
}

function setStockId(stockId){
	this.stockId = stockId;
}

function setDocumentType(documentType){
	this.documentType = documentType;
}

function setFromDate(fromDate){
	this.fromDate = fromDate;
}

function setToDate(to){
	this.to = to;
}

function setTitle(title){
	this.title = title;
}

function setSearchType(searchType){
	this.searchType = searchType;
}

function setT1code(t1code){
	this.t1code = t1code;
}

function setT2Gcode(t2Gcode){
	this.t2Gcode = t2Gcode;
}

function setT2code(t2code){
	this.t2code = t2code;
}

function setCurrLoadRecordCnt(recordCnt){
	this.currLoadRecordCnt = recordCnt;
}

function setMarket(market){
	this.market = market;
}


function setLang(lang){
	if(lang == 'E'){
		this.lang = 'E';
	}else {
		this.lang = 'zh';
	}
}

function updateCurrLoadRecordCnt(recordCnt){
	this.currLoadRecordCnt = Number(this.currLoadRecordCnt) + Number(recordCnt);
}


function getTitleSearchCriteria(){
	var category = $('input#selectedSecurities').val();
	var stockId = $('input#stockId').val();
	var documentType = $('input#selectedDocType').val();
	var fromDate = $('input#startDate').val();
	var toDate = $('input#endDate').val();
	var title = $('input#newsTitle').val();
	var searchType = $('input#searchTypeInt').val();
	var t1code = $('input#tierOneId').val();
	var t2Gcode = $('input#tierTwoGpId').val();
	var t2code = $('input#tierTwoId').val();

	fromDate = replaceCharacters(fromDate, '-', '');
	toDate = replaceCharacters(toDate, '-', '');
	
	setLang($('input#lang').val()); 
	setCategory(category);
	setStockId(stockId);
	setDocumentType(documentType);
	setFromDate(fromDate);
	setToDate(toDate);
	setTitle(title);
	setSearchType(searchType);
	setT1code(t1code);
	setT2Gcode(t2Gcode);
	setT2code(t2code);
}

function getTotalRecordTxt(lang, loadedRecord){
	var result = '';
	
	if(lang == 'E'){
		result = 'Total records found: ' + loadedRecord;
	}else {
		result = '\u5171\u6709 ' + loadedRecord + ' \u7D00\u9304';
	}
	return result;
}

function getLoadMore(lang){
	var result = '';
	
	if(lang == 'E'){
		result = 'LOAD MORE';
	}else {
		result = '\u66F4\u591A';
	}
	return result;
}

function getRealseTimeLabel(lang){
	var result = '';
	
	if(lang == 'E'){
		result = this.lbl_releaseTime_en;
	}else {
		result = this.lbl_releaseTime_zh;
	}
	
	return result;
}

function getStockCodeLabel(lang){	
	var result = '';
	
	if(lang == 'E'){
		result = this.lbl_stockCode_en;
	}else {
		result = this.lbl_stockCode_zh;
	}
	return result;
}

function getStockNameLabel(lang){
	var result = '';
	
	if(lang == 'E'){
		result = this.lbl_StockName_en;
	}else {
		result = this.lbl_StockName_zh;
	}
	return result;
}

function getDocumentLabel(lang){
	var result = '';
	
	if(lang == 'E'){
		result = this.lbl_Document_en;
	}else {
		result = this.lbl_Document_zh;
	}
	return result;
}

function getShowing(lang,loadedRecord,recordCnt){
	var result = '';
	if(lang == 'E'){
		result = 'Showing ' + loadedRecord + ' of ' + recordCnt + ' records';
	}else {
		result = '\u986f\u793a' + loadedRecord + '\u7d00\u9304\uff0c\u5171\u6709' + recordCnt + '\u7d00\u9304';
	}
	return result;
}

function notFoundText(lang){
	var result = '';
	
	if(lang == 'E'){
		result = 'No matches found';
	}else {
		result = '\u6C92\u6709\u627E\u5230\u8A0A\u606F';
	}
	return result;
}

function getStockNameLabel(lang){
	var result = '';
	
	if(lang == 'E'){
		result = this.lbl_StockName_en;
	}else {
		result = this.lbl_StockName_zh;
	}
	
	return result;
}


function getDodWebpathLabel(lang){
	var result = '';
	
	if(lang == 'E'){
		result = this.lbl_dodWebpath_en;
	}else {
		result = this.lbl_dodWebpath_zh;
	}
	
	return result;
}

function clearTable(){
	$('.search-page-container table.table tbody').empty();
	$('#recordCountPanel2').empty();
	$('table.table').trigger('update');
}