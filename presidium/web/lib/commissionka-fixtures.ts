// Opt-in examples only. Never seeded into D1 or used to create orders/verification.
import type {Listing,Category,Condition,SellerType} from './commissionka';
import credits from '../public/commissionka/credits.json';
function image(id:string){const credit=credits.find(c=>c.id===id)!;return {id,url:'/commissionka/'+id+'.webp',thumbnail:'/commissionka/'+id+'-thumb.webp',credit:{author:credit.author,license:credit.license,url:credit.url,source:credit.source}}}
const samples:{id:string;title:string;category:Category;condition:Condition;price:number;city:string;photos:string[];specs:Record<string,string>;type?:SellerType}[]=[
 {id:'camera',title:'Fujifilm X100V',category:'electronics',condition:'used',price:142000,city:'Москва',photos:['camera'],specs:{Модель:'X100V',Объектив:'23 мм'}},
 {id:'headphones',title:'Sony WH-1000XM3',category:'electronics',condition:'used',price:14500,city:'Санкт-Петербург',photos:['headphones','headphones-detail'],specs:{Подключение:'Bluetooth',Тип:'Полноразмерные'}},
 {id:'chair',title:'Кресло из гнутой фанеры',category:'home',condition:'used',price:27000,city:'Москва',photos:['chair'],specs:{Материал:'Фанера',Тип:'Кресло'}},
 {id:'drill',title:'Аккумуляторная дрель Makita LXT',category:'tools',condition:'new',price:18500,city:'Казань',photos:['drill'],specs:{Платформа:'LXT',Тип:'Аккумуляторная'},type:'company'},
 {id:'watch',title:'Apple Watch Series 4 · 40 мм',category:'electronics',condition:'used',price:9500,city:'Екатеринбург',photos:['watch'],specs:{Размер:'40 мм',Корпус:'Алюминий'}},
 {id:'bicycle',title:'Складной велосипед Brompton',category:'other',condition:'new',price:168000,city:'Санкт-Петербург',photos:['bicycle'],specs:{Конструкция:'Складная'},type:'manufacturer'},
];
export const marketFixtures:Listing[]=samples.map((s,i)=>({id:'fixture-'+s.id,title:s.title,description:'Учебный пример карточки. Фотография показывает модель товара, а не реальную вещь продавца. Цена, город и характеристики служат для проверки интерфейса; связаться с продавцом или оформить сделку нельзя.',category:s.category,condition:s.condition,priceKopecks:s.price*100,city:s.city,specs:s.specs,images:s.photos.map(image),seller:{id:'fixture-seller-'+i,name:'Демонстрационный продавец',type:s.type||'private',identity:'unverified',rightToSell:'unverified',ownership:'unverified',manufacturer:'unverified'},status:'active',conditionVerified:false,revision:1,createdAt:1700000000000-i,updatedAt:1700000000000-i,priceHistory:[],source:'fixture'}));
